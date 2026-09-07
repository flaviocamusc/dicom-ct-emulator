# DICOM CT Modality Emulator

Herramienta de pruebas para emular una modalidad CT y validar integraciones DICOM en entornos de desarrollo y QA. No es un producto médico, no realiza diagnóstico y no debe conectarse a datos clínicos reales sin controles adicionales.

Emulador de modalidad CT para pruebas de integración DICOM:

- `C-FIND` contra Modality Worklist (SCP).
- Generación de una instancia CT sintética a partir de un paciente seleccionado.
- `C-STORE` como SCU hacia un PACS o servidor de almacenamiento.
- Servidor MWL SCP local con SQLite y administración desde la interfaz gráfica Tkinter.

## Estado del proyecto

El proyecto está orientado a pruebas locales y de integración. Actualmente implementa MWL C-FIND y CT C-STORE; todavía no incluye TLS, autenticación de usuarios, auditoría clínica, Enhanced CT ni persistencia multiusuario distribuida.

## Requisitos

- Python 3.10 o superior.
- Acceso de red al PACS/MWL.

## Estructura

| Archivo | Responsabilidad |
|---|---|
| `app.py` | GUI, cliente MWL, generación CT y cliente C-STORE |
| `database.py` | Esquema y operaciones SQLite para órdenes MWL |
| `mwl_server.py` | MWL SCP y matching de consultas C-FIND |
| `requirements.txt` | Dependencias Python |
| `AI_EXTENSION_GUIDE.md` | Contexto y reglas para extensiones con agentes IA |
| `LICENSE` | Licencia MIT |

## Instalación y ejecución

```powershell
cd C:\Users\flavi\Downloads\dicom-ct-emulator
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py app.py
```

## Configuración de ejemplo

Los campos de la aplicación representan dos destinos independientes:

| Campo | Ejemplo |
|---|---|
| AE local | `CT_EMULATOR` |
| AE destino MWL | `MWL_SCP` |
| Host MWL | `127.0.0.1` |
| Puerto MWL | `42424` |
| AE destino Store | `PACS` |
| Host Store | `127.0.0.1` |
| Puerto Store | `11112` |

El AE Title DICOM debe tener como máximo 16 caracteres ASCII. El servidor MWL local usa `127.0.0.1:42424` por defecto para evitar puertos privilegiados.

## Servidor MWL local

La pestaña **Servidor MWL** administra órdenes CT en `mwl.sqlite3` y publica el SOP Class `Modality Worklist Information Model - FIND` como SCP. Permite:

- Iniciar y detener el servidor DICOM.
- Elegir AE Title, host y puerto.
- Crear y eliminar órdenes.
- Cargar dos órdenes de ejemplo.
- Ver las órdenes persistidas en SQLite.

Para probar todo en una sola instancia:

1. Abre **Servidor MWL** y pulsa **Cargar ejemplos**.
2. Pulsa **Iniciar SCP**.
3. En **Modalidad CT**, deja `MWL host=127.0.0.1`, `MWL puerto=42424`, `MWL AE=MWL_SCP`.
4. Pulsa **Consultar MWL (C-FIND)**.
5. Selecciona una orden y genera la imagen CT.

La base de datos se crea automáticamente al iniciar la aplicación. Es local y no está pensada todavía para concurrencia multiusuario, autenticación ni auditoría clínica.

## Flujo de prueba

1. Configura el destino MWL y pulsa **Consultar MWL**.
2. Selecciona una fila recibida.
3. Configura el destino C-STORE y pulsa **Generar CT** o **Generar y enviar CT**.
4. Revisa el log y confirma el estudio en el PACS.

La imagen generada es sintética, de 512 x 512 píxeles, modalidad `CT`, `MONOCHROME2`, 16 bits con signo y sin datos clínicos reales. No debe utilizarse para diagnóstico.

## Consideraciones DICOM

- MWL utiliza `1.2.840.10008.5.1.4.31` (Modality Worklist Information Model - FIND).
- C-STORE usa CT Image Storage (`1.2.840.10008.5.1.4.1.1.2`).
- El emulador actúa como SCU para MWL/C-STORE y también puede actuar como SCP MWL local.
- El servidor remoto debe estar configurado para aceptar el AE local y sus IP/puerto.
- Para TLS, autenticación, compresión o soporte de Enhanced CT se requiere ampliar la configuración y negociar los contextos correspondientes.

## Verificaciones de desarrollo

```powershell
python -m py_compile app.py database.py mwl_server.py
python -m pip check
```

La prueba de integración local levanta el MWL SCP, agrega órdenes sintéticas y ejecuta un C-FIND SCU contra `127.0.0.1:42424`.

## Licencia y autoría asistida por IA

Este proyecto se distribuye bajo la licencia MIT. La implementación inicial fue creada con asistencia de IA mediante OpenCode y el modelo `gpt-5.6-luna` (`opencode-go/gpt-5.6-luna`). El código debe ser revisado y validado por una persona responsable antes de utilizarse en cualquier entorno clínico o con información sanitaria.

Las contribuciones son bienvenidas. Para extender el proyecto con otro agente IA, consultar `AI_EXTENSION_GUIDE.md`.
