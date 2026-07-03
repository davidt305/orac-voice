# Orac Voice for macOS: installation

Estimated time: 15-20 minutes, mostly model downloads.

## Requirements

- macOS 13+ (Apple Silicon recommended; Intel works but slower)
- [Homebrew](https://brew.sh)
- 8 GB RAM minimum (16 GB recommended)
- ~5 GB free disk (models included)

## Step 1: local engines

```bash
brew install whisper-cpp ollama
brew services start ollama
ollama pull llama3.2:3b
```

## Step 2: Python dependencies

Inside the project folder:

```bash
python3 -m venv .venv
.venv/bin/pip install sounddevice pyobjc
```

## Step 3: the Whisper model

Pick by RAM ( → About This Mac):

| RAM | Model | Size | Download |
|-----|-------|------|----------|
| 16 GB+ | `ggml-large-v3-turbo-q5_0.bin` | ~574 MB | https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin |
| 8 GB | `ggml-small-q8_0.bin` | ~264 MB | https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin |

Save it into `models/`. If you picked `small`, change `whisper_model` in `config.json`:

```json
"whisper_model": "models/ggml-small-q8_0.bin",
```

If your whisper-server is not at `/opt/homebrew/bin/whisper-server` (Intel Macs: `/usr/local/bin/...`), adjust `whisper_server_bin` in `config.json`.

## Step 4: build the app and grant permissions

```bash
chmod +x make-app.sh && ./make-app.sh
```

This creates **Orac Voice** in /Applications (with icon and ad-hoc signature, so macOS permission prompts and lists say "Orac Voice" instead of Terminal or Python). Open it from Applications and macOS will ask for:

1. **Microphone**: accept.
2. **Input Monitoring** and **Accessibility**: System Settings → Privacy & Security, enable "Orac Voice" in both lists, then open the app again.

Debug alternative (live log in the terminal): `.venv/bin/python flow.py`. In that case permissions get requested for your terminal app.

## Step 5: try it

1. Open Notes, click into the text.
2. Hold **Fn**, say something, release.
3. The floating pill appears and the text is pasted by itself.

Voice-free alternative test:

```bash
.venv/bin/python flow.py --test windows/test-audio.wav
```

It should print the RAW and CLEAN text lines.

## Cloud mode (Groq, optional)

Run transcription + cleanup on Groq's free API instead of the local engines. Use it when the machine is too weak for local whisper: a 60 s dictation drops from ~20-30 s of waiting to ~1-2 s. Trade-off: **your voice audio is sent to Groq** (they don't train on API data, but it does leave your machine).

1. Create a free API key (no credit card) at https://console.groq.com/keys
2. Save it in a file named `groq_key.txt` next to `flow.py`. It is gitignored: never commit it, never put it in `config.json`. Alternative: set the `GROQ_API_KEY` environment variable.
3. In `config.json`: `"provider": "groq"`
4. Restart the app.

With `provider: groq`, Steps 1 and 3 (whisper.cpp + model) and Ollama are unused and can be skipped or uninstalled. Free tier limits (8 h of audio and 1,000 cleanups per day) are far beyond real dictation use. Groq-mode troubleshooting:

- `CERTIFICATE_VERIFY_FAILED` (python.org installs): run `Install Certificates.command` inside `/Applications/Python 3.x/`.
- `model_decommissioned` (HTTP 400): Groq retired a model; update `groq_stt_model` / `groq_chat_model` in `config.json` per https://console.groq.com/docs/deprecations

## Daily use

See the [README](README.md#usage). Settings at http://127.0.0.1:8091 (or the 🎙 menu).

## Troubleshooting

- **The key does nothing**: the Step 4 permissions are missing, or were granted to a different app identity. The process prints the exact fix on startup.
- **whisper-server won't start**: run `whisper-server -m models/<model>.bin --host 127.0.0.1 --port 8090` by hand to see the error.
- **USB mic won't open** (e.g. 48kHz-only interfaces): already handled, it opens at native rate and resamples.
- **Log**: `.tmp/orac.log` (app mode) or the terminal itself.
