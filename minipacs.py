from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from pydicom import dcmread
from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    CTImageStorage,
    PatientRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)


class MiniPacsDatabase:
    def __init__(self, path: Path, storage_dir: Path) -> None:
        self.path = path
        self.storage_dir = storage_dir
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS instances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sop_instance_uid TEXT NOT NULL UNIQUE,
                    sop_class_uid TEXT NOT NULL,
                    study_instance_uid TEXT NOT NULL DEFAULT '',
                    series_instance_uid TEXT NOT NULL DEFAULT '',
                    patient_id TEXT NOT NULL DEFAULT '',
                    patient_name TEXT NOT NULL DEFAULT '',
                    accession_number TEXT NOT NULL DEFAULT '',
                    study_date TEXT NOT NULL DEFAULT '',
                    modality TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_instances_study ON instances(study_instance_uid)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_instances_patient ON instances(patient_id)")

    def store(self, dataset: Dataset) -> Path:
        sop_instance_uid = str(dataset.SOPInstanceUID)
        safe_uid = re.sub(r"[^0-9A-Za-z_.-]", "_", sop_instance_uid)
        path = self.storage_dir / f"{safe_uid}.dcm"
        dataset.save_as(path, write_like_original=False)
        values = (
            sop_instance_uid, str(dataset.SOPClassUID), str(getattr(dataset, "StudyInstanceUID", "")),
            str(getattr(dataset, "SeriesInstanceUID", "")), str(getattr(dataset, "PatientID", "")),
            str(getattr(dataset, "PatientName", "")), str(getattr(dataset, "AccessionNumber", "")),
            str(getattr(dataset, "StudyDate", "")), str(getattr(dataset, "Modality", "")),
            str(path), datetime.now().isoformat(timespec="seconds"),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO instances (
                    sop_instance_uid, sop_class_uid, study_instance_uid, series_instance_uid,
                    patient_id, patient_name, accession_number, study_date, modality, file_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sop_instance_uid) DO UPDATE SET
                    file_path=excluded.file_path, created_at=excluded.created_at
                """,
                values,
            )
        return path

    def list_items(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM instances ORDER BY study_date, patient_id, id").fetchall()
        return [dict(row) for row in rows]

    def get_item(self, sop_instance_uid: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM instances WHERE sop_instance_uid = ?", (sop_instance_uid,)).fetchone()
        return dict(row) if row else None

    def delete(self, item_id: int) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT file_path FROM instances WHERE id = ?", (item_id,)).fetchone()
            connection.execute("DELETE FROM instances WHERE id = ?", (item_id,))
        if row:
            Path(row["file_path"]).unlink(missing_ok=True)

    def find(self, identifier: Dataset) -> list[dict[str, Any]]:
        requested_level = str(getattr(identifier, "QueryRetrieveLevel", "STUDY")).upper()
        filters: list[tuple[str, str]] = []
        for keyword, column in (("PatientID", "patient_id"), ("PatientName", "patient_name"), ("StudyInstanceUID", "study_instance_uid"), ("AccessionNumber", "accession_number"), ("StudyDate", "study_date"), ("Modality", "modality")):
            value = str(getattr(identifier, keyword, "")).strip()
            if value:
                filters.append((column, value))
        clauses = []
        parameters: list[str] = []
        for column, value in filters:
            if "*" in value or "?" in value:
                clauses.append(f"LOWER({column}) GLOB LOWER(?)")
                parameters.append(value.replace("?", "?"))
            else:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(f"SELECT * FROM instances{where} ORDER BY study_date, patient_id, id", parameters).fetchall()
        items = [dict(row) for row in rows]
        if requested_level == "PATIENT":
            unique: dict[str, dict[str, Any]] = {}
            for item in items:
                unique.setdefault(item["patient_id"], item)
            return list(unique.values())
        if requested_level == "STUDY":
            unique = {}
            for item in items:
                unique.setdefault(item["study_instance_uid"], item)
            return list(unique.values())
        return items

    @staticmethod
    def to_dataset(item: dict[str, Any], identifier: Dataset | None = None) -> Dataset:
        result = Dataset()
        for keyword, column in (("PatientName", "patient_name"), ("PatientID", "patient_id"), ("StudyInstanceUID", "study_instance_uid"), ("SeriesInstanceUID", "series_instance_uid"), ("SOPInstanceUID", "sop_instance_uid"), ("SOPClassUID", "sop_class_uid"), ("AccessionNumber", "accession_number"), ("StudyDate", "study_date"), ("Modality", "modality")):
            setattr(result, keyword, item[column])
        result.QueryRetrieveLevel = str(getattr(identifier, "QueryRetrieveLevel", "STUDY")) if identifier else "STUDY"
        return result


class MiniPacsServer:
    def __init__(self, database: MiniPacsDatabase, log: Any) -> None:
        self.database = database
        self.log = log
        self.server = None
        self.destinations: dict[str, tuple[str, int]] = {}

    @property
    def running(self) -> bool:
        return self.server is not None and self.server.is_alive()

    def start(self, ae_title: str, host: str, port: int, destinations: dict[str, tuple[str, int]]) -> None:
        if self.running:
            raise RuntimeError("El MiniPACS ya está iniciado")
        self.destinations = {key.strip().upper(): value for key, value in destinations.items()}
        ae = AE(ae_title=ae_title)
        ae.add_supported_context(CTImageStorage)
        # The server also opens an internal SCU association for C-MOVE delivery.
        ae.add_requested_context(CTImageStorage)
        for context in (PatientRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelFind, PatientRootQueryRetrieveInformationModelMove, StudyRootQueryRetrieveInformationModelMove):
            ae.add_supported_context(context)
        handlers = [
            (evt.EVT_C_STORE, self._on_store),
            (evt.EVT_C_FIND, self._on_find),
            (evt.EVT_C_MOVE, self._on_move),
        ]
        self.server = ae.start_server((host, port), block=False, evt_handlers=handlers)
        self.log(f"MiniPACS SCP iniciado en {host}:{port}, AE={ae_title}")

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server = None
            self.log("MiniPACS SCP detenido")

    def _on_store(self, event: Any) -> int:
        dataset = event.dataset
        dataset.file_meta = event.file_meta
        path = self.database.store(dataset)
        self.log(f"MiniPACS C-STORE: {path.name}")
        return 0x0000

    def _on_find(self, event: Any):
        items = self.database.find(event.identifier)
        self.log(f"MiniPACS C-FIND: {len(items)} resultado(s)")
        for item in items:
            yield 0xFF00, self.database.to_dataset(item, event.identifier)
        yield 0x0000, None

    def _on_move(self, event: Any):
        destination = event.move_destination
        if isinstance(destination, bytes):
            destination = destination.decode("ascii", errors="ignore")
        destination = str(destination).strip().upper()
        address = self.destinations.get(destination)
        if address is None:
            self.log(f"MiniPACS C-MOVE: destino no configurado {destination}")
            yield 0xA801, None
            return
        items = self.database.find(event.identifier)
        self.log(f"MiniPACS C-MOVE: {len(items)} instancia(s) hacia {destination}")
        yield address
        yield len(items)
        for item in items:
            yield 0xFF00, dcmread(item["file_path"])
