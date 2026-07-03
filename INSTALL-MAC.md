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

## Paso 4: crear la app y dar permisos

```bash
chmod +x make-app.sh && ./make-app.sh
```

Esto crea **Orac Voice** en /Applications (con ícono y firma ad-hoc, para que los permisos aparezcan a nombre de "Orac Voice" y no de Terminal o Python). Ábrela desde Aplicaciones y macOS va a pedir:

1. **Micrófono**: aceptar.
2. **Input Monitoring** y **Accessibility**: System Settings → Privacy & Security, activar "Orac Voice" en ambas listas, y abrir la app de nuevo.

Alternativa para debug (log en vivo en la terminal): `.venv/bin/python flow.py`. En ese caso los permisos se piden para tu Terminal.

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

## Solución de problemas

- **La tecla no hace nada**: faltan los permisos del Paso 4, o los diste a otra app. El proceso imprime el error exacto al arrancar.
- **whisper-server no levanta**: correr `whisper-server -m models/<modelo>.bin --host 127.0.0.1 --port 8090` a mano para ver el error.
- **Mic USB no abre** (ej. interfaces 48kHz): ya está manejado, se abre a la frecuencia nativa y se remuestrea.
- **Log**: `.tmp/orac.log` (modo app) o la propia Terminal.
