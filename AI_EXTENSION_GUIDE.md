# Guía para Extender el Proyecto con un Agente IA

Este archivo sirve como contexto inicial para otro agente de IA que vaya a trabajar sobre el repositorio.

## Contexto del proyecto

`dicom-ct-emulator` es una herramienta de pruebas DICOM para una modalidad CT. Está escrita en Python y usa Tkinter para la interfaz, `pydicom` para datasets y `pynetdicom` para asociaciones DICOM.

Componentes principales:

- `app.py`: aplicación Tkinter, cliente MWL C-FIND, generador CT sintético y cliente C-STORE SCU.
- `database.py`: repositorio SQLite de órdenes MWL.
- `mwl_server.py`: servidor MWL SCP basado en `EVT_C_FIND`.
- `minipacs.py`: repositorio de instancias y servidor SCP para C-STORE, C-FIND y C-MOVE.
- `requirements.txt`: dependencias de ejecución.
- `README.md`: instalación, flujo de pruebas y limitaciones.
- `LICENSE`: licencia MIT.

## Reglas de trabajo

1. Mantener los cambios enfocados y preservar el flujo existente de C-FIND y C-STORE.
2. No incluir `.venv`, `mwl.sqlite3`, archivos dentro de `generated/`, secretos ni datos clínicos reales.
3. No usar datos de pacientes reales en pruebas o fixtures.
4. Validar AE Titles con la restricción DICOM de 16 caracteres ASCII.
5. Mantener las operaciones de red fuera del hilo de la interfaz.
6. Documentar cualquier nuevo SOP Class, Transfer Syntax o comportamiento de asociación.
7. No presentar la herramienta como producto clínico ni como software diagnóstico.

## Flujo recomendado para un agente

1. Leer `README.md`, `app.py`, `database.py` y `mwl_server.py` antes de editar.
2. Describir el cambio propuesto y sus riesgos DICOM.
3. Implementar el cambio con el menor número de piezas nuevas posible.
4. Ejecutar las verificaciones locales:

```powershell
python -m py_compile app.py database.py mwl_server.py
python -m pip check
```

5. Si se modifica MWL, probar una asociación C-FIND local con órdenes sintéticas.
6. Actualizar `README.md` y este archivo si cambia la arquitectura.
7. Revisar `git diff` y confirmar que no se publican datos sensibles.

## Ideas de extensión

- Soporte para otras modalidades mediante una tabla de modalidades y validación de códigos DICOM.
- C-ECHO y verificación de conectividad desde la GUI.
- C-STORE SCP de pruebas con almacenamiento separado.
- Exportación/importación de órdenes en CSV o JSON sin datos identificables.
- Pruebas automatizadas con un puerto efímero y una base SQLite temporal.
- TLS DICOM con certificados configurables.
- Enhanced CT y negociación explícita de Transfer Syntax.
- Indexación por series e instancias para consultas Query/Retrieve más completas.
- Destinos C-MOVE persistidos y validación de AE Titles remotos.

## Nota sobre IA

La versión inicial fue creada con asistencia de IA usando OpenCode y el modelo `gpt-5.6-luna` (`opencode-go/gpt-5.6-luna`). Toda contribución posterior debe ser revisada por una persona con conocimientos de DICOM antes de usarse en una red clínica.
