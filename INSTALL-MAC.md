# Orac Voice para macOS: instalación

Tiempo estimado: 15-20 minutos, casi todo descarga de modelos.

## Requisitos

- macOS 13+ (Apple Silicon recomendado; en Intel funciona más lento)
- [Homebrew](https://brew.sh)
- 8 GB de RAM mínimo (16 GB recomendado)
- ~5 GB de disco libre (modelos incluidos)

## Paso 1: motores locales

```bash
brew install whisper-cpp ollama
brew services start ollama
ollama pull llama3.2:3b
```

## Paso 2: dependencias Python

Dentro de la carpeta del proyecto:

```bash
python3 -m venv .venv
.venv/bin/pip install sounddevice pyobjc
```

## Paso 3: el modelo de Whisper

Elegir según la RAM (→ Apple → Acerca de esta Mac):

| RAM | Modelo | Tamaño | Descarga |
|-----|--------|--------|----------|
| 16 GB o más | `ggml-large-v3-turbo-q5_0.bin` | ~574 MB | https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin |
| 8 GB | `ggml-small-q8_0.bin` | ~264 MB | https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin |

Guardar en `models/`. Si usaste `small`, cambiar `whisper_model` en `config.json`:

```json
"whisper_model": "models/ggml-small-q8_0.bin",
```

Si tu whisper-server no quedó en `/opt/homebrew/bin/whisper-server` (Mac Intel: `/usr/local/bin/...`), ajustar `whisper_server_bin` en `config.json`.

## Paso 4: arrancar y dar permisos

```bash
.venv/bin/python flow.py
```

La primera vez macOS va a pedir (para tu Terminal, o para la app con que lo lances):

1. **Micrófono**: aceptar.
2. **Input Monitoring** y **Accessibility**: System Settings → Privacy & Security. Activar, cerrar la Terminal y volver a correr.

## Paso 5: probar

1. Abrir Notas, click en el texto.
2. Mantener **Fn**, decir "probando, probando, uno, dos, tres", soltar.
3. Aparece la pastilla flotante y el texto se pega solo.

Prueba alternativa sin voz:

```bash
.venv/bin/python flow.py --test windows/test-audio.wav
```

Debe imprimir el texto crudo y el limpio.

## Uso diario

Ver el [README](README.md#uso). Ajustes en http://127.0.0.1:8091 (o menú 🎙).

## Opcional: launcher como app

Para no depender de la Terminal, se puede envolver el comando en una app con Automator (Run Shell Script → `cd <carpeta> && .venv/bin/python -u flow.py >> .tmp/orac.log 2>&1 &`) y guardarla en /Applications. Ojo: los permisos TCC (mic, teclado) quedan atados a esa app y hay que otorgarlos de nuevo la primera vez.

## Solución de problemas

- **La tecla no hace nada**: faltan los permisos del Paso 4, o los diste a otra app. El proceso imprime el error exacto al arrancar.
- **whisper-server no levanta**: correr `whisper-server -m models/<modelo>.bin --host 127.0.0.1 --port 8090` a mano para ver el error.
- **Mic USB no abre** (ej. interfaces 48kHz): ya está manejado, se abre a la frecuencia nativa y se remuestrea.
- **Log**: `.tmp/orac.log` (modo app) o la propia Terminal.
