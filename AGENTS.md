# Orac Voice: guide for AI agents

You are installing or operating **Orac Voice**, a fully local push-to-talk dictation app. Nothing leaves the machine: transcription is whisper.cpp over localhost, filler-word cleanup is Ollama over localhost, and the result is pasted at the user's cursor.

## Repo layout

```
flow.py            macOS daemon (hotkey, audio, pipeline, settings server on 127.0.0.1:8091)
pill.py            macOS floating pill (NSPanel, PyObjC)
settings.html      settings page, served from disk by the daemon (offline, no CDN)
config.json        macOS config
fonts/             bundled fonts (offline)
models/            whisper .bin model goes here (gitignored)
INSTALL-MAC.md     human install guide, macOS (Spanish)
windows/           SELF-CONTAINED Windows port: its own flow.py, pill.py (tkinter),
                   config.json, settings.html, fonts/, launcher "Orac Voice.vbs",
                   INSTALL.md (Spanish), test_logic.py, test-audio.wav
```

Only 4 functions differ per platform (hotkey listener, clipboard, paste, sounds), all in the "plataforma" section of each `flow.py`. Everything else is identical between the two versions: if you fix shared logic in one, mirror it in the other.

## Install: macOS

```bash
brew install whisper-cpp ollama
brew services start ollama
ollama pull llama3.2:3b
python3 -m venv .venv && .venv/bin/pip install sounddevice pyobjc
# model (16GB+ RAM; use ggml-small-q8_0.bin for 8GB and update config.json)
curl -L -o models/ggml-large-v3-turbo-q5_0.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin
.venv/bin/python flow.py
```

macOS will require Microphone, Input Monitoring and Accessibility permissions for the launching app (Terminal). The daemon prints exact instructions if the event tap fails. TCC permissions are the most common failure: they bind to the app identity that launched the process.

## Install: Windows

Work inside the `windows/` folder (it is self-contained; it can be copied alone to the target PC).

1. Python 3.11+ from python.org, WITH "Add python.exe to PATH" checked.
2. `pip install sounddevice pynput`
3. whisper.cpp Windows binaries from https://github.com/ggml-org/whisper.cpp/releases: `whisper-bin-x64.zip` (CPU) or `whisper-cublas-*-bin-x64.zip` (NVIDIA). Extract so that `windows\whisper-bin\whisper-server.exe` exists (flatten any `Release/` subfolder).
4. Model into `windows\models\`: `ggml-large-v3-turbo-q5_0.bin` (16GB+ RAM) or `ggml-small-q8_0.bin` (8GB, then update `whisper_model` in config.json). Download from https://huggingface.co/ggerganov/whisper.cpp/tree/main
5. Ollama from https://ollama.com/download/windows then `ollama pull llama3.2:3b`
6. Launch with `Orac Voice.vbs` (runs `pythonw flow.py` hidden). First run asks for microphone permission.

## Verification checklist (run in order, all must pass)

1. `python --version` ≥ 3.11
2. `pip show sounddevice pynput` (Windows) / `pyobjc` present in venv (macOS)
3. whisper binary exists (`whisper-bin\whisper-server.exe` on Windows; `which whisper-server` on macOS)
4. The `.bin` referenced by `whisper_model` in config.json exists under `models/`
5. `ollama list` includes `llama3.2:3b`
6. Logic self-test (no audio needed, any OS): `python windows/test_logic.py` prints `TODO OK`
7. Headless E2E (whisper + ollama, no mic): `python flow.py --test <path-to>/test-audio.wav` prints CRUDO and LIMPIO lines
8. Launch the daemon; `curl http://127.0.0.1:8091/api/state` returns JSON

## Local API (daemon must be running)

- `GET /api/state` → config, mic list, history, dictionary
- `POST /api/config` → any of `{"hotkey": {...}, "language": "auto|es|en", "mic": "<name>"}`
- `POST /api/capture/start` then poll `GET /api/capture` → interactive hotkey rebind
- `POST /api/history/clear` (POST, not GET), `POST /api/history/delete {"ts": ...}`
- `POST /api/dict/record {"written": "n8n"}` → records ~2.5s of mic, returns what whisper heard; saved to `dictionary.json`
- `POST /api/dict/delete {"written": "n8n"}`

The 8091 bind is the single-instance lock: if `GET /api/state` answers, the app is already running (a second launch just opens the settings page and exits).

## Rules

- Do NOT edit `system_prompt` in config.json unless explicitly asked: it is calibrated for Chilean Spanish + English code-switching, with a deterministic guard in `clean()` that falls back to the raw transcript if the LLM rewrites instead of only deleting fillers.
- Do NOT commit `history.jsonl`, `dictionary.json`, `models/`, `whisper-bin/`, `.tmp/` (already gitignored): they contain user data or large binaries.
- Restart after editing `flow.py` or `config.json` (settings.html is re-read per request; a browser refresh is enough for it).
- macOS restart: `kill $(pgrep -f flow.py)` then relaunch. Windows restart: end `pythonw.exe` in Task Manager, double-click the .vbs.
