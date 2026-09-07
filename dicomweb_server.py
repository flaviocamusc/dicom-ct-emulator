from __future__ import annotations

import io
import struct
import threading
import webbrowser
import zlib
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request
from pydicom import dcmread
from werkzeug.serving import make_server

from minipacs import MiniPacsDatabase


def _dicom_json(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    def value(vr: str, content: Any) -> dict[str, Any]:
        return {"vr": vr, "Value": [content]}

    return {
        "00080018": value("UI", item["sop_instance_uid"]),
        "00080016": value("UI", item["sop_class_uid"]),
        "00080020": value("DA", item["study_date"]),
        "00080050": value("SH", item["accession_number"]),
        "00080060": value("CS", item["modality"]),
        "00100010": value("PN", {"Alphabetic": item["patient_name"]}),
        "00100020": value("LO", item["patient_id"]),
        "0020000D": value("UI", item["study_instance_uid"]),
        "0020000E": value("UI", item["series_instance_uid"]),
    }


def _png_from_ct(path: Path) -> bytes:
    dataset = dcmread(path)
    rows, columns = int(dataset.Rows), int(dataset.Columns)
    raw = dataset.PixelData
    samples = struct.unpack("<{}h".format(rows * columns), raw[: rows * columns * 2])
    low, high = -1000, 1000
    pixels = bytearray(max(0, min(255, int((sample - low) * 255 / (high - low)))) for sample in samples)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    scanlines = b"".join(b"\x00" + pixels[row * columns : (row + 1) * columns] for row in range(rows))
    header = struct.pack(">IIBBBBB", columns, rows, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scanlines, 6)) + chunk(b"IEND", b"")


def create_app(database: MiniPacsDatabase) -> Flask:
    app = Flask(__name__)

    @app.after_request
    def cors(response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    @app.route("/")
    def viewer() -> Response:
        return Response(VIEWER_HTML, mimetype="text/html")

    @app.route("/api/studies")
    def api_studies() -> Response:
        studies: dict[str, dict[str, Any]] = {}
        for item in database.list_items():
            studies.setdefault(item["study_instance_uid"], {
                "studyInstanceUID": item["study_instance_uid"],
                "patientID": item["patient_id"],
                "patientName": item["patient_name"],
                "studyDate": item["study_date"],
                "modality": item["modality"],
                "instances": 0,
            })["instances"] += 1
        return jsonify(list(studies.values()))

    @app.route("/api/preview/<study_uid>/<sop_uid>.png")
    def preview(study_uid: str, sop_uid: str) -> Response:
        item = database.get_item(sop_uid)
        if not item or item["study_instance_uid"] != study_uid:
            return Response("Not found", status=404)
        return Response(_png_from_ct(Path(item["file_path"])), mimetype="image/png")

    @app.route("/dicom-web/studies", methods=["GET", "POST", "OPTIONS"])
    def studies() -> Response:
        if request.method == "OPTIONS":
            return Response(status=204)
        if request.method == "POST":
            if not request.data:
                return jsonify({"error": "Empty DICOM body"}), 400
            dataset = dcmread(io.BytesIO(request.data))
            database.store(dataset)
            return Response(status=200, headers={"Content-Type": "application/dicom+json"})
        result = []
        seen = set()
        for item in database.list_items():
            if request.args.get("PatientID") and item["patient_id"] != request.args["PatientID"]:
                continue
            if request.args.get("PatientName") and request.args["PatientName"].lower() not in item["patient_name"].lower():
                continue
            if request.args.get("StudyInstanceUID") and item["study_instance_uid"] != request.args["StudyInstanceUID"]:
                continue
            if item["study_instance_uid"] not in seen:
                result.append(_dicom_json(item))
                seen.add(item["study_instance_uid"])
        return jsonify(result)

    @app.route("/dicom-web/studies/<study_uid>/series")
    def series(study_uid: str) -> Response:
        items = [item for item in database.list_items() if item["study_instance_uid"] == study_uid]
        result, seen = [], set()
        for item in items:
            if item["series_instance_uid"] not in seen:
                result.append(_dicom_json(item))
                seen.add(item["series_instance_uid"])
        return jsonify(result)

    @app.route("/dicom-web/studies/<study_uid>/series/<series_uid>/instances")
    def instances(study_uid: str, series_uid: str) -> Response:
        items = [item for item in database.list_items() if item["study_instance_uid"] == study_uid and item["series_instance_uid"] == series_uid]
        return jsonify([_dicom_json(item) for item in items])

    @app.route("/dicom-web/studies/<study_uid>/metadata")
    def study_metadata(study_uid: str) -> Response:
        items = [item for item in database.list_items() if item["study_instance_uid"] == study_uid]
        return jsonify([_dicom_json(item) for item in items])

    @app.route("/dicom-web/studies/<study_uid>/series/<series_uid>/metadata")
    def series_metadata(study_uid: str, series_uid: str) -> Response:
        items = [item for item in database.list_items() if item["study_instance_uid"] == study_uid and item["series_instance_uid"] == series_uid]
        return jsonify([_dicom_json(item) for item in items])

    @app.route("/dicom-web/studies/<study_uid>/series/<series_uid>/instances/<sop_uid>")
    def instance(study_uid: str, series_uid: str, sop_uid: str) -> Response:
        item = database.get_item(sop_uid)
        if not item or item["study_instance_uid"] != study_uid or item["series_instance_uid"] != series_uid:
            return Response("Not found", status=404)
        data = Path(item["file_path"]).read_bytes()
        boundary = "dicomweb-boundary"
        body = f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode() + data + f"\r\n--{boundary}--\r\n".encode()
        return Response(body, content_type=f"multipart/related; type=application/dicom; boundary={boundary}")

    @app.route("/dicom-web/studies/<study_uid>/series/<series_uid>/instances/<sop_uid>/frames/1")
    def rendered_frame(study_uid: str, series_uid: str, sop_uid: str) -> Response:
        item = database.get_item(sop_uid)
        if not item or item["study_instance_uid"] != study_uid or item["series_instance_uid"] != series_uid:
            return Response("Not found", status=404)
        return Response(_png_from_ct(Path(item["file_path"])), mimetype="image/png")

    return app


class DicomWebServer:
    def __init__(self, database: MiniPacsDatabase, log: Any) -> None:
        self.database = database
        self.log = log
        self.server = None
        self.thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.server is not None

    def start(self, host: str, port: int) -> None:
        if self.running:
            raise RuntimeError("El servidor DICOMweb ya está iniciado")
        self.server = make_server(host, port, create_app(self.database), threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.log(f"DICOMweb iniciado en http://{host}:{port}")

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server = None
            self.thread = None
            self.log("DICOMweb detenido")

    def open_viewer(self, host: str, port: int) -> None:
        webbrowser.open(f"http://{host}:{port}/")


VIEWER_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MiniPACS DICOMweb Viewer</title>
<style>body{margin:0;background:#10151b;color:#e8eef5;font:14px Segoe UI,Arial,sans-serif}header{padding:18px 24px;border-bottom:1px solid #2b3540}h1{margin:0;font-size:20px}main{display:grid;grid-template-columns:360px 1fr;min-height:calc(100vh - 69px)}aside{border-right:1px solid #2b3540;padding:16px;overflow:auto}section{padding:20px;display:flex;align-items:center;justify-content:center;background:#080b0f}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px 6px;border-bottom:1px solid #2b3540;font-size:12px}tr{cursor:pointer}tr:hover{background:#1e2935}.hint{color:#9daab7;font-size:12px}.viewer{max-width:90%;max-height:80vh;background:#000;border:1px solid #394754}.tag{color:#8ec5ff}.error{color:#ff9b9b}</style></head>
<body><header><h1>MiniPACS DICOMweb Viewer</h1><div class="hint">Visor local de pruebas · QIDO-RS / WADO-RS</div></header><main><aside><button onclick="loadStudies()">Actualizar estudios</button><p id="count" class="hint"></p><table><thead><tr><th>Paciente</th><th>Fecha</th><th>Mod.</th></tr></thead><tbody id="studies"></tbody></table><p id="message" class="hint"></p></aside><section><div id="empty" class="hint">Selecciona un estudio para ver su instancia</div><img id="image" class="viewer" hidden alt="Previsualización CT"></section></main>
<script>const api='/api';
async function loadStudies(){const body=document.querySelector('#studies');body.innerHTML='';document.querySelector('#image').hidden=true;document.querySelector('#empty').hidden=false;try{const data=await (await fetch(api+'/studies')).json();document.querySelector('#count').textContent=data.length+' estudio(s)';for(const study of data){const row=document.createElement('tr');row.innerHTML='<td>'+escapeHtml(study.patientName)+'<br><span class="tag">'+escapeHtml(study.patientID)+'</span></td><td>'+escapeHtml(study.studyDate)+'</td><td>'+escapeHtml(study.modality)+'</td>';row.onclick=()=>loadInstance(study);body.appendChild(row)}}catch(error){document.querySelector('#message').textContent=error;document.querySelector('#message').className='error'}}
async function loadInstance(study){const image=document.querySelector('#image');const empty=document.querySelector('#empty');try{const series=await (await fetch('/dicom-web/studies/'+encodeURIComponent(study.studyInstanceUID)+'/series')).json();const first=series[0];const instances=await (await fetch('/dicom-web/studies/'+encodeURIComponent(study.studyInstanceUID)+'/series/'+encodeURIComponent(first['0020000E'].Value[0])+'/instances')).json();const sop=instances[0]['00080018'].Value[0];image.src=api+'/preview/'+encodeURIComponent(study.studyInstanceUID)+'/'+encodeURIComponent(sop)+'.png';image.hidden=false;empty.hidden=true}catch(error){empty.textContent='No se pudo cargar la imagen: '+error;empty.className='error'}}
function escapeHtml(text){return String(text??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}loadStudies();</script></body></html>"""
