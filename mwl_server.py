from __future__ import annotations

from threading import Event
from typing import Any

from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import ModalityWorklistInformationFind

from database import WorklistDatabase


class WorklistServer:
    def __init__(self, database: WorklistDatabase, log: Any) -> None:
        self.database = database
        self.log = log
        self.server = None
        self._stopped = Event()

    @property
    def running(self) -> bool:
        return self.server is not None and self.server.is_alive()

    def start(self, ae_title: str, host: str, port: int) -> None:
        if self.running:
            raise RuntimeError("El servidor MWL ya está iniciado")
        ae = AE(ae_title=ae_title)
        ae.add_supported_context(ModalityWorklistInformationFind)
        handlers = [(evt.EVT_C_FIND, self._on_find)]
        self._stopped.clear()
        self.server = ae.start_server((host, port), block=False, evt_handlers=handlers)
        self.log(f"MWL SCP iniciado en {host}:{port}, AE={ae_title}")

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server = None
            self.log("MWL SCP detenido")

    def _on_find(self, event: Any):
        identifier = event.identifier
        request = self._request_values(identifier)
        self.log(f"C-FIND recibido desde AE={event.assoc.requestor.ae_title}")
        for item in self.database.list_items():
            if self._matches(item, request):
                yield 0xFF00, self._to_dataset(item, identifier)
        yield 0x0000, None

    @staticmethod
    def _request_values(identifier: Dataset) -> dict[str, str]:
        steps = getattr(identifier, "ScheduledProcedureStepSequence", [])
        step = steps[0] if steps else Dataset()
        return {
            "patient_id": str(getattr(identifier, "PatientID", "")),
            "patient_name": str(getattr(identifier, "PatientName", "")),
            "accession_number": str(getattr(identifier, "AccessionNumber", "")),
            "modality": str(getattr(step, "Modality", "")),
            "station_ae_title": str(getattr(step, "ScheduledStationAETitle", "")),
            "scheduled_date": str(getattr(step, "ScheduledProcedureStepStartDate", "")),
        }

    @staticmethod
    def _matches(item: dict[str, Any], request: dict[str, str]) -> bool:
        for field in ("patient_id", "patient_name", "accession_number", "modality", "station_ae_title"):
            query = request[field].strip()
            if query and query != "*" and not WorklistServer._wildcard_match(str(item[field]), query):
                return False
        date_query = request["scheduled_date"].strip()
        if date_query and not WorklistServer._date_match(str(item["scheduled_date"]), date_query):
            return False
        return True

    @staticmethod
    def _wildcard_match(value: str, query: str) -> bool:
        import re
        pattern = "^" + re.escape(query).replace(r"\*", ".*").replace(r"\?", ".") + "$"
        return re.match(pattern, value, flags=re.IGNORECASE) is not None

    @staticmethod
    def _date_match(value: str, query: str) -> bool:
        if "-" in query:
            start, end = query.split("-", 1)
            return (not start or value >= start) and (not end or value <= end)
        return WorklistServer._wildcard_match(value, query)

    @staticmethod
    def _to_dataset(item: dict[str, Any], request: Dataset) -> Dataset:
        result = Dataset()
        for tag in ("PatientName", "PatientID", "PatientBirthDate", "PatientSex", "AccessionNumber", "StudyInstanceUID", "RequestingPhysician"):
            source = {"PatientName": "patient_name", "PatientID": "patient_id", "PatientBirthDate": "birth_date", "PatientSex": "sex", "AccessionNumber": "accession_number", "StudyInstanceUID": "study_instance_uid", "RequestingPhysician": "requesting_physician"}[tag]
            setattr(result, tag, item.get(source, ""))
        step = Dataset()
        step.ScheduledStationAETitle = item["station_ae_title"]
        step.ScheduledProcedureStepStartDate = item["scheduled_date"]
        step.ScheduledProcedureStepStartTime = item["scheduled_time"]
        step.Modality = item["modality"]
        step.ScheduledPerformingPhysicianName = item["performing_physician"]
        step.ScheduledProcedureStepDescription = item["procedure_description"]
        step.ScheduledProcedureStepID = item["scheduled_step_id"]
        result.ScheduledProcedureStepSequence = [step]
        return result
