# Orac Voice para Windows: instalación

Dictado por voz 100% local. Mantienes **Right Ctrl**, hablas, sueltas, y el texto limpio se pega donde esté tu cursor. Nada sale de tu computador.

Esta guía sirve para una persona o para un agente de IA (Claude, ChatGPT, etc.) ejecutando los pasos. Tiempo estimado: 15-20 minutos, casi todo descarga de modelos.

## Requisitos

- Windows 10 22H2 o Windows 11, 64-bit
- 8 GB de RAM mínimo (16 GB recomendado)
- ~5 GB de disco libre (modelos incluidos)

## Paso 1: Python

1. Descargar Python 3.11 o superior desde https://www.python.org/downloads/
2. Al instalar, marcar **"Add python.exe to PATH"** (crítico, no saltarse).
3. Verificar en una terminal (PowerShell):
   ```
   python --version
   ```
   Debe responder `Python 3.11.x` o superior.

## Paso 2: dependencias Python

```
pip install sounddevice pynput
```

Son las únicas dos. Todo lo demás es librería estándar.

## Paso 3: whisper.cpp (el motor de transcripción)

1. Ir a https://github.com/ggml-org/whisper.cpp/releases (release más reciente).
2. Descargar el zip de binarios para Windows:
   - CPU (cualquier laptop): `whisper-bin-x64.zip`
   - Con GPU NVIDIA: el zip `whisper-cublas-*-bin-x64.zip` (más rápido)
3. Extraer el contenido dentro de esta carpeta, en `whisper-bin/`.
4. Verificar que exista `whisper-bin\whisper-server.exe`:
   ```
   dir whisper-bin\whisper-server.exe
   ```
   Si el zip trae los .exe en una subcarpeta (p. ej. `Release/`), mover los archivos para que el .exe quede directo en `whisper-bin\`.

Nota: si Windows SmartScreen bloquea el .exe la primera vez, click en "More info" y luego "Run anyway".

## Paso 4: el modelo de Whisper

Elegir según la RAM del equipo (Configuración → Sistema → Acerca de):

| RAM | Modelo | Tamaño | Descarga |
|-----|--------|--------|----------|
| 16 GB o más | `ggml-large-v3-turbo-q5_0.bin` | ~574 MB | https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin |
| 8 GB | `ggml-small-q8_0.bin` | ~264 MB | https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin |

Guardar el archivo en la carpeta `models/` de esta carpeta.

Si usaste el modelo `small`, editar `config.json` y cambiar la línea `whisper_model` a:
```json
"whisper_model": "models/ggml-small-q8_0.bin",
```

## Paso 5: Ollama (el limpiador de muletillas)

1. Descargar e instalar desde https://ollama.com/download/windows
2. En una terminal:
   ```
   ollama pull llama3.2:3b
   ```
   (~2 GB de descarga. Ollama queda corriendo como app de bandeja; dejarlo así.)

## Paso 6: arrancar

Doble click a **`Orac Voice.vbs`**.

- La primera vez, Windows va a pedir permiso de micrófono: aceptar.
- El primer arranque tarda unos segundos (carga del modelo).
- Doble click de nuevo con la app ya corriendo abre la página de ajustes (http://127.0.0.1:8091).

## Paso 7: probar

1. Abrir el Bloc de notas (Notepad).
2. Mantener presionada la tecla **Ctrl derecha**, decir "probando, probando, uno, dos, tres", y soltar.
3. Aparece una pastilla negra abajo al centro mientras grabas, y el texto se pega solo.

Prueba alternativa sin voz (terminal, en esta carpeta):
```
python flow.py --test test-audio.wav
```
Debe imprimir el texto crudo y el limpio.

## Uso diario

| Acción | Cómo |
|--------|------|
| Dictar | Mantener Right Ctrl mientras hablas, soltar al terminar |
| Manos libres | Doble-tap a Right Ctrl; un tap más para terminar |
| Cancelar | Escape |
| Ajustes, historial y diccionario | Doble click a Orac Voice.vbs (o http://127.0.0.1:8091) |
| Cambiar la tecla de dictado | En ajustes, click al botón de la tecla y presionar la nueva |
| Palabras propias (siglas, marcas) | En ajustes → Dictionary: escribir la palabra como debe quedar, click Record y decirla una vez |
| Cerrar la app | Botón "Quit Orac Voice" al final de la página de ajustes |

## Arranque automático al prender el equipo (opcional)

1. `Win + R`, escribir `shell:startup`, Enter.
2. Click derecho sobre `Orac Voice.vbs` → Mostrar más opciones → Crear acceso directo, y mover ese acceso directo a la carpeta que se abrió.

## Solución de problemas

- **No pasa nada al dictar**: revisar el log en `.tmp\orac.log` dentro de esta carpeta.
- **"pythonw no se reconoce"**: Python quedó fuera del PATH. Reinstalar marcando "Add python.exe to PATH".
- **El texto no se pega pero suena el "listo"**: el texto SIEMPRE queda en el clipboard; pegar con Ctrl+V a mano. Pasa en apps corriendo como administrador.
- **Whisper lento**: cambiar al modelo `small` (Paso 4) o usar el binario cublas si hay GPU NVIDIA.
- **Reiniciar la app**: botón "Quit Orac Voice" al final de la página de ajustes, y doble click al .vbs de nuevo. (Plan B: Administrador de tareas → terminar `pythonw.exe`; en ese caso whisper-server puede quedar corriendo, no es problema, el próximo arranque lo reutiliza.)
- **La tecla Alt abre el menú de la app al soltarla**: es comportamiento de Windows con Alt; usar Right Ctrl o un Shift como tecla de dictado.

## Notas para agentes de IA

- Esta carpeta es autocontenida: `flow.py` (daemon), `pill.py` (indicador visual), `settings.html` (UI servida en 127.0.0.1:8091), `config.json` (configuración), `fonts/` (offline).
- El bind del puerto 8091 es el candado de instancia única: si responde `GET /api/state`, la app ya corre.
- API local: `GET /api/state` (config + mics + historial + diccionario), `POST /api/config`, `POST /api/history/clear`, `POST /api/dict/record`, `POST /api/dict/delete`, `POST /api/quit` (cierre limpio).
- Verificación de instalación correcta, en orden: (1) `python --version` ≥ 3.11, (2) `pip show sounddevice pynput`, (3) existe `whisper-bin\whisper-server.exe`, (4) existe el `.bin` en `models\` y coincide con `whisper_model` de config.json, (5) `ollama list` incluye `llama3.2:3b`, (6) `python flow.py --test test-audio.wav` imprime CRUDO y LIMPIO, (7) lanzar el .vbs y `curl http://127.0.0.1:8091/api/state` responde JSON.
- No editar `system_prompt` de config.json salvo pedido explícito: está calibrado para español chileno + inglés mezclados.
