from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class WorklistDatabase:
    """Small SQLite repository for MWL scheduled procedure steps."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS worklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id TEXT NOT NULL,
                    patient_name TEXT NOT NULL,
                    birth_date TEXT DEFAULT '',
                    sex TEXT DEFAULT 'O',
                    accession_number TEXT DEFAULT '',
                    study_instance_uid TEXT DEFAULT '',
                    station_ae_title TEXT DEFAULT '',
                    scheduled_date TEXT NOT NULL,
                    scheduled_time TEXT DEFAULT '',
                    modality TEXT NOT NULL DEFAULT 'CT',
                    performing_physician TEXT DEFAULT '',
                    procedure_description TEXT DEFAULT '',
                    scheduled_step_id TEXT DEFAULT '',
                    requesting_physician TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )

    def list_items(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM worklist ORDER BY scheduled_date, scheduled_time, id").fetchall()
        return [dict(row) for row in rows]

    def add(self, values: dict[str, str]) -> int:
        required = ("patient_id", "patient_name", "scheduled_date")
        if any(not values.get(field, "").strip() for field in required):
            raise ValueError("Patient ID, Patient Name y fecha programada son obligatorios")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO worklist (
                    patient_id, patient_name, birth_date, sex, accession_number,
                    study_instance_uid, station_ae_title, scheduled_date, scheduled_time,
                    modality, performing_physician, procedure_description,
                    scheduled_step_id, requesting_physician, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values.get("patient_id", "").strip(), values.get("patient_name", "").strip(),
                    values.get("birth_date", "").strip(), values.get("sex", "O").strip() or "O",
                    values.get("accession_number", "").strip(), values.get("study_instance_uid", "").strip(),
                    values.get("station_ae_title", "").strip(), values.get("scheduled_date", "").strip(),
                    values.get("scheduled_time", "").strip(), values.get("modality", "CT").strip() or "CT",
                    values.get("performing_physician", "").strip(), values.get("procedure_description", "").strip(),
                    values.get("scheduled_step_id", "").strip(), values.get("requesting_physician", "").strip(),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            return int(cursor.lastrowid)

    def delete(self, item_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM worklist WHERE id = ?", (item_id,))

    def seed(self) -> int:
        examples = [
            {"patient_id": "MWL001", "patient_name": "GARCIA^ANA", "birth_date": "19850612", "sex": "F", "accession_number": "ACC0001", "scheduled_date": datetime.now().strftime("%Y%m%d"), "scheduled_time": "090000", "modality": "CT", "procedure_description": "CT TORAX", "scheduled_step_id": "SPS0001", "station_ae_title": "CT_EMULATOR"},
            {"patient_id": "MWL002", "patient_name": "ROJAS^MARIO", "birth_date": "19721103", "sex": "M", "accession_number": "ACC0002", "scheduled_date": datetime.now().strftime("%Y%m%d"), "scheduled_time": "103000", "modality": "CT", "procedure_description": "CT ABDOMEN", "scheduled_step_id": "SPS0002", "station_ae_title": "CT_EMULATOR"},
        ]
        return sum(1 for item in examples if self.add(item))
