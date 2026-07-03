# Orac Voice for Windows: installation

100% local voice dictation. Hold **Right Ctrl**, speak, release, and the clean text is pasted wherever your cursor is. Nothing leaves your computer.

This guide works for a person or for an AI agent (Claude, ChatGPT, etc.) executing the steps. Estimated time: 15-20 minutes, mostly model downloads.

## Requirements

- Windows 10 22H2 or Windows 11, 64-bit
- 8 GB RAM minimum (16 GB recommended)
- ~5 GB free disk (models included)

## Step 1: Python

1. Download Python 3.11 or newer from https://www.python.org/downloads/
2. During install, check **"Add python.exe to PATH"** (critical, do not skip).
3. Verify in a terminal (PowerShell):
   ```
   python --version
   ```
   It must answer `Python 3.11.x` or newer.

## Step 2: Python dependencies

```
pip install sounddevice pynput
```

Those are the only two. Everything else is standard library.

## Step 3: whisper.cpp (the transcription engine)

1. Go to https://github.com/ggml-org/whisper.cpp/releases (latest release).
2. Download the Windows binaries zip:
   - CPU (any laptop): `whisper-bin-x64.zip`
   - NVIDIA GPU: the `whisper-cublas-*-bin-x64.zip` zip (faster)
3. Extract the contents inside this folder, into `whisper-bin/`.
4. Verify that `whisper-bin\whisper-server.exe` exists:
   ```
   dir whisper-bin\whisper-server.exe
   ```
   If the zip has the .exe files inside a subfolder (e.g. `Release/`), move them so the .exe sits directly in `whisper-bin\`.

Note: if Windows SmartScreen blocks the .exe the first time, click "More info" then "Run anyway".

## Step 4: the Whisper model

Pick by the machine's RAM (Settings → System → About):

| RAM | Model | Size | Download |
|-----|-------|------|----------|
| 16 GB+ | `ggml-large-v3-turbo-q5_0.bin` | ~574 MB | https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin |
| 8 GB | `ggml-small-q8_0.bin` | ~264 MB | https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin |

Save the file into this folder's `models/` directory.

If you used the `small` model, edit `config.json` and change the `whisper_model` line to:
```json
"whisper_model": "models/ggml-small-q8_0.bin",
```

## Step 5: Ollama (the filler-word cleaner)

1. Download and install from https://ollama.com/download/windows
2. In a terminal:
   ```
   ollama pull llama3.2:3b
   ```
   (~2 GB download. Ollama stays running as a tray app; leave it like that.)

## Step 6: launch

Double-click **`Orac Voice.vbs`**.

- The first time, Windows will ask for microphone permission: accept.
- The first launch takes a few seconds (model loading).
- Double-clicking again while the app is running opens the settings page (http://127.0.0.1:8091).

## Step 7: test

1. Open Notepad.
2. Hold the **Right Ctrl** key, say "testing, testing, one, two, three", and release.
3. A black pill appears at the bottom center while you record, and the text pastes itself.

Voice-free alternative test (terminal, in this folder):
```
python flow.py --test test-audio.wav
```
It should print the RAW and CLEAN text lines.

## Daily use

| Action | How |
|--------|-----|
| Dictate | Hold Right Ctrl while speaking, release when done |
| Hands-free | Double-tap Right Ctrl; one more tap to finish |
| Cancel | Escape |
| Settings, history and dictionary | Double-click Orac Voice.vbs (or http://127.0.0.1:8091) |
| Change the dictation key | In settings, click the key button and press the new key |
| Custom words (acronyms, brands) | Settings → Dictionary: type the word as it should be written, click Record and say it once |
| Quit the app | "Quit Orac Voice" button at the bottom of the settings page |

## Start automatically on boot (optional)

1. `Win + R`, type `shell:startup`, Enter.
2. Right-click `Orac Voice.vbs` → Show more options → Create shortcut, and move that shortcut into the folder that opened.

## Troubleshooting

- **Nothing happens when dictating**: check the log at `.tmp\orac.log` inside this folder.
- **"pythonw is not recognized"**: Python is not on PATH. Reinstall checking "Add python.exe to PATH".
- **Text doesn't paste but the "done" sound plays**: the text is ALWAYS in the clipboard; paste manually with Ctrl+V. Happens in apps running as administrator.
- **Whisper is slow**: switch to the `small` model (Step 4) or use the cublas binary if there's an NVIDIA GPU.
- **Restart the app**: "Quit Orac Voice" button at the bottom of the settings page, then double-click the .vbs again. (Plan B: Task Manager → end `pythonw.exe`; in that case whisper-server may stay running, which is fine, the next launch reuses it.)
- **The Alt key opens the app's menu when released**: that's Windows behavior with Alt; use Right Ctrl or a Shift key as the dictation key.

## Notes for AI agents

- This folder is self-contained: `flow.py` (daemon), `pill.py` (visual indicator), `settings.html` (UI served at 127.0.0.1:8091), `config.json` (configuration), `fonts/` (offline).
- The port 8091 bind is the single-instance lock: if `GET /api/state` answers, the app is already running.
- Local API: `GET /api/state` (config + mics + history + dictionary), `POST /api/config`, `POST /api/history/clear`, `POST /api/dict/record`, `POST /api/dict/delete`, `POST /api/quit` (clean shutdown).
- Install verification, in order: (1) `python --version` ≥ 3.11, (2) `pip show sounddevice pynput`, (3) `whisper-bin\whisper-server.exe` exists, (4) the `.bin` in `models\` exists and matches `whisper_model` in config.json, (5) `ollama list` includes `llama3.2:3b`, (6) `python flow.py --test test-audio.wav` prints RAW and CLEAN, (7) launch the .vbs and `curl http://127.0.0.1:8091/api/state` returns JSON.
- Do not edit `system_prompt` in config.json unless explicitly asked: it is calibrated for mixed Chilean Spanish + English.
