from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid
from pynetdicom import AE, evt
from pynetdicom.sop_class import ModalityWorklistInformationFind

from database import WorklistDatabase
from minipacs import MiniPacsDatabase, MiniPacsServer
from mwl_server import WorklistServer
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelMove


APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "generated"
DATABASE_PATH = APP_DIR / "mwl.sqlite3"
MINIPACS_DATABASE_PATH = APP_DIR / "minipacs.sqlite3"
MINIPACS_STORAGE_DIR = APP_DIR / "minipacs_storage"


def valid_ae_title(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 16 or any(ord(char) > 127 or char in "\\^" for char in value):
        raise ValueError("El AE Title debe tener entre 1 y 16 caracteres ASCII y no contener ^ ni \\")
    return value


def port(value: str) -> int:
    result = int(value)
    if not 1 <= result <= 65535:
        raise ValueError("El puerto debe estar entre 1 y 65535")
    return result


def make_mwl_query(patient_id: str, patient_name: str, date_from: str, date_to: str) -> Dataset:
    query = Dataset()
    query.PatientName = patient_name.strip() or "*"
    query.PatientID = patient_id.strip() or "*"
    query.PatientBirthDate = ""
    query.PatientSex = ""
    query.AccessionNumber = ""
    query.StudyInstanceUID = ""
    query.RequestingPhysician = ""
    query.ScheduledProcedureStepSequence = [Dataset()]
    step = query.ScheduledProcedureStepSequence[0]
    step.ScheduledStationAETitle = ""
    step.ScheduledProcedureStepStartDate = f"{date_from}-{date_to}" if date_from and date_to else (date_from or date_to)
    step.ScheduledProcedureStepStartTime = ""
    step.Modality = "CT"
    step.ScheduledPerformingPhysicianName = ""
    step.ScheduledProcedureStepDescription = ""
    step.ScheduledProcedureStepID = ""
    return query


def generate_ct_file(patient: Dataset, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    study_uid = getattr(patient, "StudyInstanceUID", "") or generate_uid()
    series_uid = generate_uid()
    sop_uid = generate_uid()
    filename = output_dir / f"CT_{getattr(patient, 'PatientID', 'UNKNOWN')}_{sop_uid.rsplit('.', 1)[-1]}.dcm"

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid(prefix="1.2.826.0.1.3680043.10.543.")

    ds = FileDataset(str(filename), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.Modality = "CT"
    ds.SeriesNumber = "1"
    ds.InstanceNumber = "1"
    ds.PatientName = getattr(patient, "PatientName", "TEST^PATIENT")
    ds.PatientID = getattr(patient, "PatientID", "TEST001")
    ds.PatientBirthDate = getattr(patient, "PatientBirthDate", "")
    ds.PatientSex = getattr(patient, "PatientSex", "O") or "O"
    ds.StudyDate = getattr(patient, "StudyDate", "") or now.strftime("%Y%m%d")
    ds.StudyTime = now.strftime("%H%M%S")
    ds.StudyID = getattr(patient, "StudyID", "CTEMU") or "CTEMU"
    ds.AccessionNumber = getattr(patient, "AccessionNumber", "")
    ds.StudyDescription = "Synthetic CT test image"
    ds.Manufacturer = "DICOM CT Modality Emulator"
    ds.PatientPosition = "HFS"
    ds.SliceThickness = "5"
    ds.KVP = "120"
    ds.Rows = 512
    ds.Columns = 512
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1
    ds.RescaleIntercept = "-1024"
    ds.RescaleSlope = "1"
    ds.ImagePositionPatient = ["0", "0", "0"]
    ds.ImageOrientationPatient = ["1", "0", "0", "0", "1", "0"]
    ds.PixelSpacing = ["0.7", "0.7"]

    pixels = bytearray()
    center = 255.5
    for y in range(512):
        for x in range(512):
            distance = ((x - center) ** 2 + (y - center) ** 2) ** 0.5
            value = max(-1024, min(1200, int(800 - distance * 3)))
            pixels.extend(int(value).to_bytes(2, byteorder="little", signed=True))
    ds.PixelData = bytes(pixels)
    ds.save_as(filename, write_like_original=False)
    return filename


class DicomApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DICOM CT Modality Emulator")
        self.geometry("1120x720")
        self.minsize(900, 600)
        self.results: list[Dataset] = []
        self.database = WorklistDatabase(DATABASE_PATH)
        self.mwl_server = WorklistServer(self.database, self.write_log)
        self.minipacs_database = MiniPacsDatabase(MINIPACS_DATABASE_PATH, MINIPACS_STORAGE_DIR)
        self.minipacs_server = MiniPacsServer(self.minipacs_database, self.write_log)
        self._build_variables()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _build_variables(self) -> None:
        self.local_ae = tk.StringVar(value="CT_EMULATOR")
        self.mwl_ae = tk.StringVar(value="MWL_SCP")
        self.mwl_host = tk.StringVar(value="127.0.0.1")
        self.mwl_port = tk.StringVar(value="42424")
        self.store_ae = tk.StringVar(value="PACS")
        self.store_host = tk.StringVar(value="127.0.0.1")
        self.store_port = tk.StringVar(value="11112")
        self.patient_id = tk.StringVar()
        self.patient_name = tk.StringVar()
        self.date_from = tk.StringVar()
        self.date_to = tk.StringVar()
        self.status = tk.StringVar(value="Listo")
        self.server_ae = tk.StringVar(value="MWL_SCP")
        self.server_host = tk.StringVar(value="127.0.0.1")
        self.server_port = tk.StringVar(value="42424")
        self.mwl_form: dict[str, tk.StringVar] = {}
        self.minipacs_ae = tk.StringVar(value="MINIPACS")
        self.minipacs_host = tk.StringVar(value="127.0.0.1")
        self.minipacs_port = tk.StringVar(value="42425")
        self.move_destination_ae = tk.StringVar(value="MINIPACS")
        self.move_destination_host = tk.StringVar(value="127.0.0.1")
        self.move_destination_port = tk.StringVar(value="42425")
        self.minipacs_patient_id = tk.StringVar()
        self.minipacs_patient_name = tk.StringVar()
        self.minipacs_study_uid = tk.StringVar()
        self.minipacs_results: list[Dataset] = []

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        container = ttk.Frame(self, padding=14)
        container.pack(fill="both", expand=True)
        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)
        root = ttk.Frame(notebook, padding=2)
        server_tab = ttk.Frame(notebook, padding=2)
        minipacs_tab = ttk.Frame(notebook, padding=2)
        notebook.add(root, text="Modalidad CT")
        notebook.add(server_tab, text="Servidor MWL")
        notebook.add(minipacs_tab, text="MiniPACS")
        ttk.Label(root, text="DICOM CT Modality Emulator", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="C-FIND Modality Worklist y C-STORE SCU para pruebas de integración").pack(anchor="w", pady=(0, 12))

        config = ttk.LabelFrame(root, text="Configuración DICOM", padding=10)
        config.pack(fill="x")
        fields = [
            ("AE local", self.local_ae), ("MWL AE", self.mwl_ae), ("MWL host", self.mwl_host),
            ("MWL puerto", self.mwl_port), ("Store AE", self.store_ae), ("Store host", self.store_host),
            ("Store puerto", self.store_port),
        ]
        for index, (label, variable) in enumerate(fields):
            row, column = divmod(index, 4)
            ttk.Label(config, text=label).grid(row=row * 2, column=column, sticky="w", padx=5, pady=(0, 2))
            ttk.Entry(config, textvariable=variable, width=22).grid(row=row * 2 + 1, column=column, sticky="ew", padx=5, pady=(0, 7))
        for column in range(4):
            config.columnconfigure(column, weight=1)

        query_frame = ttk.LabelFrame(root, text="Modality Worklist", padding=10)
        query_frame.pack(fill="x", pady=(12, 0))
        query_fields = [("Patient ID", self.patient_id), ("Patient Name", self.patient_name), ("Fecha desde YYYYMMDD", self.date_from), ("Fecha hasta YYYYMMDD", self.date_to)]
        for column, (label, variable) in enumerate(query_fields):
            ttk.Label(query_frame, text=label).grid(row=0, column=column, sticky="w", padx=5)
            ttk.Entry(query_frame, textvariable=variable).grid(row=1, column=column, sticky="ew", padx=5)
            query_frame.columnconfigure(column, weight=1)
        ttk.Button(query_frame, text="Consultar MWL (C-FIND)", command=self.query_mwl).grid(row=1, column=4, padx=5)

        table_frame = ttk.Frame(root)
        table_frame.pack(fill="both", expand=True, pady=(12, 0))
        columns = ("patient_id", "patient_name", "birth_date", "accession", "procedure", "scheduled_date")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {"patient_id": "Patient ID", "patient_name": "Patient Name", "birth_date": "Birth Date", "accession": "Accession", "procedure": "Procedure", "scheduled_date": "Scheduled Date"}
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(column, width=140, anchor="w")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="Generar CT", command=self.generate_ct).pack(side="left")
        ttk.Button(actions, text="Generar y enviar C-STORE", command=self.generate_and_store).pack(side="left", padx=8)
        ttk.Button(actions, text="Enviar archivo DICOM...", command=self.choose_and_store).pack(side="left")
        ttk.Label(actions, textvariable=self.status).pack(side="right")
        log_frame = ttk.LabelFrame(root, text="Log", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.log = tk.Text(log_frame, height=7, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        self._build_mwl_server_ui(server_tab)
        self._build_minipacs_ui(minipacs_tab)

    def _build_mwl_server_ui(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Servidor Modality Worklist", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(parent, text="SCP DICOM local respaldado por SQLite para pruebas de C-FIND").pack(anchor="w", pady=(0, 12))

        connection = ttk.LabelFrame(parent, text="AE del servidor", padding=10)
        connection.pack(fill="x")
        for column, (label, variable) in enumerate((("AE Title", self.server_ae), ("Host", self.server_host), ("Puerto", self.server_port))):
            ttk.Label(connection, text=label).grid(row=0, column=column, sticky="w", padx=5)
            ttk.Entry(connection, textvariable=variable, width=24).grid(row=1, column=column, sticky="ew", padx=5)
            connection.columnconfigure(column, weight=1)
        ttk.Button(connection, text="Iniciar SCP", command=self.start_mwl_server).grid(row=1, column=3, padx=5)
        ttk.Button(connection, text="Detener SCP", command=self.stop_mwl_server).grid(row=1, column=4, padx=5)
        self.server_status = ttk.Label(connection, text="Detenido")
        self.server_status.grid(row=1, column=5, padx=10)

        form = ttk.LabelFrame(parent, text="Nueva orden CT", padding=10)
        form.pack(fill="x", pady=(12, 0))
        fields = (("Patient ID", "patient_id"), ("Patient Name", "patient_name"), ("Birth Date", "birth_date"), ("Sex", "sex"), ("Accession", "accession_number"), ("Fecha YYYYMMDD", "scheduled_date"), ("Hora HHMMSS", "scheduled_time"), ("Procedimiento", "procedure_description"), ("SPS ID", "scheduled_step_id"), ("Station AE", "station_ae_title"))
        for index, (label, key) in enumerate(fields):
            variable = tk.StringVar(value="O" if key == "sex" else (datetime.now().strftime("%Y%m%d") if key == "scheduled_date" else ("CT_EMULATOR" if key == "station_ae_title" else "")))
            self.mwl_form[key] = variable
            row, column = divmod(index, 5)
            ttk.Label(form, text=label).grid(row=row * 2, column=column, sticky="w", padx=5, pady=(0, 2))
            ttk.Entry(form, textvariable=variable).grid(row=row * 2 + 1, column=column, sticky="ew", padx=5, pady=(0, 7))
        for column in range(5):
            form.columnconfigure(column, weight=1)
        ttk.Button(form, text="Agregar orden", command=self.add_worklist_item).grid(row=4, column=3, padx=5, sticky="e")
        ttk.Button(form, text="Cargar ejemplos", command=self.seed_worklist).grid(row=4, column=4, padx=5, sticky="w")

        table_frame = ttk.Frame(parent)
        table_frame.pack(fill="both", expand=True, pady=(12, 0))
        columns = ("id", "patient_id", "patient_name", "date", "time", "procedure", "station")
        self.worklist_table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {"id": "ID", "patient_id": "Patient ID", "patient_name": "Patient Name", "date": "Fecha", "time": "Hora", "procedure": "Procedimiento", "station": "Station AE"}
        for column in columns:
            self.worklist_table.heading(column, text=headings[column])
            self.worklist_table.column(column, width=125, anchor="w")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.worklist_table.yview)
        self.worklist_table.configure(yscrollcommand=scrollbar.set)
        self.worklist_table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ttk.Button(parent, text="Eliminar orden seleccionada", command=self.delete_worklist_item).pack(anchor="w", pady=(8, 0))
        self.refresh_worklist()

    def _build_minipacs_ui(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="MiniPACS local", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(parent, text="SCP DICOM para C-STORE, C-FIND y C-MOVE con SQLite y almacenamiento de archivos").pack(anchor="w", pady=(0, 12))

        connection = ttk.LabelFrame(parent, text="Servidor MiniPACS", padding=10)
        connection.pack(fill="x")
        for column, (label, variable) in enumerate((("AE Title", self.minipacs_ae), ("Host", self.minipacs_host), ("Puerto", self.minipacs_port))):
            ttk.Label(connection, text=label).grid(row=0, column=column, sticky="w", padx=5)
            ttk.Entry(connection, textvariable=variable, width=22).grid(row=1, column=column, sticky="ew", padx=5)
            connection.columnconfigure(column, weight=1)
        ttk.Button(connection, text="Iniciar MiniPACS", command=self.start_minipacs).grid(row=1, column=3, padx=5)
        ttk.Button(connection, text="Detener", command=self.stop_minipacs).grid(row=1, column=4, padx=5)
        ttk.Button(connection, text="Usar como C-STORE", command=self.use_minipacs_store).grid(row=1, column=5, padx=5)
        self.minipacs_status = ttk.Label(connection, text="Detenido")
        self.minipacs_status.grid(row=1, column=6, padx=10)

        destination = ttk.LabelFrame(parent, text="Destino C-MOVE", padding=10)
        destination.pack(fill="x", pady=(10, 0))
        for column, (label, variable) in enumerate((("Destino AE", self.move_destination_ae), ("Host", self.move_destination_host), ("Puerto", self.move_destination_port))):
            ttk.Label(destination, text=label).grid(row=0, column=column, sticky="w", padx=5)
            ttk.Entry(destination, textvariable=variable, width=22).grid(row=1, column=column, sticky="ew", padx=5)
            destination.columnconfigure(column, weight=1)
        ttk.Label(destination, text="El AE debe estar configurado como destino en el servidor").grid(row=1, column=3, columnspan=3, sticky="w", padx=10)

        query = ttk.LabelFrame(parent, text="Consulta MiniPACS (C-FIND Study Root)", padding=10)
        query.pack(fill="x", pady=(10, 0))
        query_fields = (("Patient ID", self.minipacs_patient_id), ("Patient Name", self.minipacs_patient_name), ("Study UID", self.minipacs_study_uid))
        for column, (label, variable) in enumerate(query_fields):
            ttk.Label(query, text=label).grid(row=0, column=column, sticky="w", padx=5)
            ttk.Entry(query, textvariable=variable).grid(row=1, column=column, sticky="ew", padx=5)
            query.columnconfigure(column, weight=1)
        ttk.Button(query, text="C-FIND", command=self.query_minipacs).grid(row=1, column=3, padx=5)
        ttk.Button(query, text="C-MOVE seleccionado", command=self.move_minipacs).grid(row=1, column=4, padx=5)

        table_frame = ttk.Frame(parent)
        table_frame.pack(fill="both", expand=True, pady=(10, 0))
        columns = ("id", "patient_id", "patient_name", "study_uid", "modality", "study_date", "accession")
        self.minipacs_table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {"id": "ID", "patient_id": "Patient ID", "patient_name": "Patient Name", "study_uid": "Study UID", "modality": "Modality", "study_date": "Study Date", "accession": "Accession"}
        for column in columns:
            self.minipacs_table.heading(column, text=headings[column])
            self.minipacs_table.column(column, width=125, anchor="w")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.minipacs_table.yview)
        self.minipacs_table.configure(yscrollcommand=scrollbar.set)
        self.minipacs_table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ttk.Button(parent, text="Eliminar instancia seleccionada", command=self.delete_minipacs_item).pack(anchor="w", pady=(8, 0))
        self.refresh_minipacs()

    def refresh_minipacs(self) -> None:
        for item in self.minipacs_table.get_children():
            self.minipacs_table.delete(item)
        for item in self.minipacs_database.list_items():
            self.minipacs_table.insert("", "end", iid=str(item["id"]), values=(item["id"], item["patient_id"], item["patient_name"], item["study_instance_uid"], item["modality"], item["study_date"], item["accession_number"]))

    def start_minipacs(self) -> None:
        try:
            ae_title = valid_ae_title(self.minipacs_ae.get())
            host = self.minipacs_host.get().strip() or "127.0.0.1"
            mini_port = port(self.minipacs_port.get())
            destination_ae = valid_ae_title(self.move_destination_ae.get())
            destination_host = self.move_destination_host.get().strip() or "127.0.0.1"
            destination_port = port(self.move_destination_port.get())
            self.minipacs_server.start(ae_title, host, mini_port, {destination_ae: (destination_host, destination_port)})
            self.minipacs_status.configure(text=f"Activo en {host}:{mini_port}")
        except (ValueError, OSError, RuntimeError) as error:
            messagebox.showerror("No se pudo iniciar el MiniPACS", str(error))

    def stop_minipacs(self) -> None:
        self.minipacs_server.stop()
        self.minipacs_status.configure(text="Detenido")

    def use_minipacs_store(self) -> None:
        self.store_ae.set(self.minipacs_ae.get())
        self.store_host.set(self.minipacs_host.get())
        self.store_port.set(self.minipacs_port.get())
        self.write_log("Destino C-STORE cambiado al MiniPACS local")

    def delete_minipacs_item(self) -> None:
        selection = self.minipacs_table.selection()
        if not selection:
            messagebox.showwarning("MiniPACS", "Selecciona una instancia para eliminar")
            return
        self.minipacs_database.delete(int(selection[0]))
        self.refresh_minipacs()
        self.write_log(f"Instancia MiniPACS eliminada: {selection[0]}")

    def minipacs_query_dataset(self) -> Dataset:
        query = Dataset()
        query.QueryRetrieveLevel = "STUDY"
        query.PatientID = self.minipacs_patient_id.get().strip() or "*"
        query.PatientName = self.minipacs_patient_name.get().strip() or "*"
        query.StudyInstanceUID = self.minipacs_study_uid.get().strip()
        query.StudyDate = ""
        query.Modality = ""
        query.AccessionNumber = ""
        return query

    def query_minipacs(self) -> None:
        try:
            settings = self.settings()
            query = self.minipacs_query_dataset()
            mini_host = self.minipacs_host.get().strip()
            mini_port = port(self.minipacs_port.get())
            mini_ae = valid_ae_title(self.minipacs_ae.get())
        except ValueError as error:
            messagebox.showerror("Configuración inválida", str(error))
            return
        self.status.set("Consultando MiniPACS...")
        threading.Thread(target=self._query_minipacs, args=(settings, query, mini_host, mini_port, mini_ae), daemon=True).start()

    def _query_minipacs(self, settings: dict[str, object], query: Dataset, mini_host: str, mini_port: int, mini_ae: str) -> None:
        try:
            ae = AE(ae_title=settings["local_ae"])
            ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
            association = ae.associate(mini_host, mini_port, ae_title=mini_ae)
            if not association.is_established:
                raise ConnectionError("No se pudo establecer la asociación C-FIND con MiniPACS")
            results = []
            for status, identifier in association.send_c_find(query, StudyRootQueryRetrieveInformationModelFind):
                if status and status.Status in (0xFF00, 0xFF01) and identifier:
                    results.append(identifier)
                if status:
                    self.write_log(f"MiniPACS C-FIND status=0x{status.Status:04X}")
            association.release()
            self.minipacs_results = results
            self.after(0, self.refresh_minipacs_query_results)
            self.after(0, lambda: self.status.set(f"{len(results)} resultado(s) MiniPACS"))
        except Exception as error:
            self.write_log(f"Error MiniPACS C-FIND: {error}")
            self.after(0, lambda: self.status.set("Error en MiniPACS C-FIND"))

    def refresh_minipacs_query_results(self) -> None:
        for item in self.minipacs_table.get_children():
            self.minipacs_table.delete(item)
        for index, item in enumerate(self.minipacs_results):
            self.minipacs_table.insert("", "end", iid=f"query-{index}", values=("", getattr(item, "PatientID", ""), getattr(item, "PatientName", ""), getattr(item, "StudyInstanceUID", ""), getattr(item, "Modality", ""), getattr(item, "StudyDate", ""), getattr(item, "AccessionNumber", "")))

    def move_minipacs(self) -> None:
        selection = self.minipacs_table.selection()
        if not selection or not selection[0].startswith("query-"):
            messagebox.showwarning("MiniPACS C-MOVE", "Ejecuta C-FIND y selecciona un resultado")
            return
        try:
            settings = self.settings()
            destination = valid_ae_title(self.move_destination_ae.get())
            mini_host = self.minipacs_host.get().strip()
            mini_port = port(self.minipacs_port.get())
            mini_ae = valid_ae_title(self.minipacs_ae.get())
            query = self.minipacs_results[int(selection[0].split("-", 1)[1])]
            move_query = Dataset()
            move_query.QueryRetrieveLevel = "STUDY"
            move_query.StudyInstanceUID = getattr(query, "StudyInstanceUID", "")
            move_query.PatientID = getattr(query, "PatientID", "")
        except ValueError as error:
            messagebox.showerror("Configuración inválida", str(error))
            return
        self.status.set("Ejecutando C-MOVE...")
        threading.Thread(target=self._move_minipacs, args=(settings, destination, move_query, mini_host, mini_port, mini_ae), daemon=True).start()

    def _move_minipacs(self, settings: dict[str, object], destination: str, query: Dataset, mini_host: str, mini_port: int, mini_ae: str) -> None:
        try:
            ae = AE(ae_title=settings["local_ae"])
            ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
            association = ae.associate(mini_host, mini_port, ae_title=mini_ae)
            if not association.is_established:
                raise ConnectionError("No se pudo establecer la asociación C-MOVE con MiniPACS")
            for status, identifier in association.send_c_move(query, destination, StudyRootQueryRetrieveInformationModelMove):
                if status:
                    self.write_log(f"MiniPACS C-MOVE status=0x{status.Status:04X}")
            association.release()
            self.after(0, lambda: self.status.set("C-MOVE completado"))
        except Exception as error:
            self.write_log(f"Error MiniPACS C-MOVE: {error}")
            self.after(0, lambda: self.status.set("Error en MiniPACS C-MOVE"))

    def refresh_worklist(self) -> None:
        for item in self.worklist_table.get_children():
            self.worklist_table.delete(item)
        for item in self.database.list_items():
            self.worklist_table.insert("", "end", iid=str(item["id"]), values=(item["id"], item["patient_id"], item["patient_name"], item["scheduled_date"], item["scheduled_time"], item["procedure_description"], item["station_ae_title"]))

    def add_worklist_item(self) -> None:
        try:
            values = {key: variable.get() for key, variable in self.mwl_form.items()}
            self.database.add(values)
            self.refresh_worklist()
            self.write_log(f"Orden MWL agregada: {values.get('patient_id', '')}")
        except (ValueError, OSError) as error:
            messagebox.showerror("No se pudo agregar la orden", str(error))

    def delete_worklist_item(self) -> None:
        selection = self.worklist_table.selection()
        if not selection:
            messagebox.showwarning("Orden MWL", "Selecciona una orden para eliminar")
            return
        self.database.delete(int(selection[0]))
        self.refresh_worklist()
        self.write_log(f"Orden MWL eliminada: {selection[0]}")

    def seed_worklist(self) -> None:
        count = self.database.seed()
        self.refresh_worklist()
        self.write_log(f"Órdenes MWL de ejemplo agregadas: {count}")

    def start_mwl_server(self) -> None:
        try:
            ae_title = valid_ae_title(self.server_ae.get())
            host = self.server_host.get().strip() or "127.0.0.1"
            server_port = port(self.server_port.get())
            self.mwl_server.start(ae_title, host, server_port)
            self.server_status.configure(text=f"Activo en {host}:{server_port}")
            self.mwl_port.set(str(server_port))
            self.mwl_ae.set(ae_title)
        except (ValueError, OSError, RuntimeError) as error:
            messagebox.showerror("No se pudo iniciar el MWL SCP", str(error))

    def stop_mwl_server(self) -> None:
        self.mwl_server.stop()
        self.server_status.configure(text="Detenido")

    def close(self) -> None:
        self.mwl_server.stop()
        self.minipacs_server.stop()
        self.destroy()

    def write_log(self, text: str) -> None:
        self.after(0, self._write_log, text)

    def _write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"[{datetime.now():%H:%M:%S}] {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def settings(self) -> dict[str, object]:
        return {"local_ae": valid_ae_title(self.local_ae.get()), "mwl_ae": valid_ae_title(self.mwl_ae.get()), "mwl_host": self.mwl_host.get().strip(), "mwl_port": port(self.mwl_port.get()), "store_ae": valid_ae_title(self.store_ae.get()), "store_host": self.store_host.get().strip(), "store_port": port(self.store_port.get())}

    def selected_patient(self) -> Dataset:
        selection = self.table.selection()
        if not selection:
            raise ValueError("Selecciona un paciente de la lista MWL")
        return self.results[int(selection[0])]

    def query_mwl(self) -> None:
        try:
            settings = self.settings()
        except ValueError as error:
            messagebox.showerror("Configuración inválida", str(error))
            return
        self.status.set("Consultando MWL...")
        threading.Thread(target=self._query_mwl, args=(settings,), daemon=True).start()

    def _query_mwl(self, settings: dict[str, object]) -> None:
        try:
            ae = AE(ae_title=settings["local_ae"])
            ae.add_requested_context(ModalityWorklistInformationFind)
            association = ae.associate(settings["mwl_host"], settings["mwl_port"], ae_title=settings["mwl_ae"])
            if not association.is_established:
                raise ConnectionError("No se pudo establecer la asociación MWL")
            query = make_mwl_query(self.patient_id.get(), self.patient_name.get(), self.date_from.get(), self.date_to.get())
            results = []
            for status, identifier in association.send_c_find(query, ModalityWorklistInformationFind):
                if status and status.Status in (0xFF00, 0xFF01) and identifier:
                    results.append(identifier)
                if status:
                    self.write_log(f"MWL C-FIND status=0x{status.Status:04X}")
            association.release()
            self.results = results
            self.after(0, self.refresh_results)
            self.write_log(f"MWL: {len(results)} resultado(s)")
            self.after(0, lambda: self.status.set(f"{len(results)} resultado(s) MWL"))
        except Exception as error:
            self.write_log(f"Error MWL: {error}")
            self.after(0, lambda: self.status.set("Error en C-FIND"))

    def refresh_results(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        for index, patient in enumerate(self.results):
            steps = getattr(patient, "ScheduledProcedureStepSequence", [])
            step = steps[0] if steps else Dataset()
            self.table.insert("", "end", iid=str(index), values=(getattr(patient, "PatientID", ""), getattr(patient, "PatientName", ""), getattr(patient, "PatientBirthDate", ""), getattr(patient, "AccessionNumber", ""), getattr(step, "ScheduledProcedureStepDescription", ""), getattr(step, "ScheduledProcedureStepStartDate", "")))

    def generate_ct(self) -> Path | None:
        try:
            patient = self.selected_patient()
            filename = generate_ct_file(patient, OUTPUT_DIR)
            self.write_log(f"CT generado: {filename}")
            self.status.set("CT generado")
            return filename
        except Exception as error:
            messagebox.showerror("No se pudo generar CT", str(error))
            return None

    def generate_and_store(self) -> None:
        filename = self.generate_ct()
        if filename:
            self.store_file(filename)

    def choose_and_store(self) -> None:
        filename = filedialog.askopenfilename(title="Seleccionar DICOM", filetypes=[("DICOM", "*.dcm"), ("Todos", "*.*")])
        if filename:
            self.store_file(Path(filename))

    def store_file(self, filename: Path) -> None:
        try:
            settings = self.settings()
        except ValueError as error:
            messagebox.showerror("Configuración inválida", str(error))
            return
        self.status.set("Enviando C-STORE...")
        threading.Thread(target=self._store_file, args=(settings, filename), daemon=True).start()

    def _store_file(self, settings: dict[str, object], filename: Path) -> None:
        try:
            from pydicom import dcmread
            dataset = dcmread(filename)
            ae = AE(ae_title=settings["local_ae"])
            ae.add_requested_context(dataset.SOPClassUID)
            association = ae.associate(settings["store_host"], settings["store_port"], ae_title=settings["store_ae"])
            if not association.is_established:
                raise ConnectionError("No se pudo establecer la asociación C-STORE")
            status = association.send_c_store(dataset)
            association.release()
            code = getattr(status, "Status", None)
            if code != 0x0000:
                raise RuntimeError(f"C-STORE rechazado, status=0x{code:04X}" if code is not None else "C-STORE sin status")
            self.write_log(f"C-STORE OK: {filename.name}, status=0x{code:04X}")
            self.after(0, lambda: self.status.set("C-STORE completado"))
        except Exception as error:
            self.write_log(f"Error C-STORE: {error}")
            self.after(0, lambda: self.status.set("Error en C-STORE"))


if __name__ == "__main__":
    DicomApp().mainloop()
