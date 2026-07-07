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
pip install sounddevice pynput onnx-asr[cpu,hub]
```

`onnx-asr` powers Parakeet, the default engine: fast even on CPU-only laptops, its model (~700MB) downloads automatically on the first run. Everything else is standard library.

## Step 3: whisper.cpp (the transcription engine)

> Using **Cloud mode (Groq)**? Skip Steps 3, 4 and 5 entirely and see the "Cloud mode" section below. On machines without an NVIDIA GPU it is also the biggest speed upgrade available.

First, check the hardware. Paste this in PowerShell:

```powershell
$gpu   = (Get-CimInstance Win32_VideoController).Name -join ", "
$ram   = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$cores = (Get-CimInstance Win32_Processor).NumberOfCores
if ($gpu -match "NVIDIA") { "$gpu | $ram GB | $cores cores -> cublas zip + large-v3-turbo model" }
else { "$gpu | $ram GB | $cores cores -> RECOMMENDED: Cloud mode (Groq). Local fallback: CPU zip + small model + whisper_threads = $cores" }
```

The GPU decides the model, not the RAM: RAM only decides what fits in memory, not how fast it runs. The prebuilt binaries only accelerate NVIDIA cards; any other graphics (Intel Iris Xe, AMD integrated, etc.) runs on pure CPU, where the large model takes ~60 s per dictation. Unusable for push-to-talk. That is why on non-NVIDIA machines the recommended setup is **Cloud mode (Groq)**: skip Steps 3-5 and follow the Cloud mode section below instead.

1. Go to https://github.com/ggml-org/whisper.cpp/releases (latest release).
2. Download the Windows binaries zip the check above picked:
   - No NVIDIA (pure CPU): `whisper-bin-x64.zip`
   - NVIDIA GPU: the `whisper-cublas-*-bin-x64.zip` zip
3. Extract the contents inside this folder, into `whisper-bin/`.
4. Verify that `whisper-bin\whisper-server.exe` exists:
   ```
   dir whisper-bin\whisper-server.exe
   ```
   If the zip has the .exe files inside a subfolder (e.g. `Release/`), move them so the .exe sits directly in `whisper-bin\`.

Note: if Windows SmartScreen blocks the .exe the first time, click "More info" then "Run anyway".

## Step 4: the Whisper model

Pick by the hardware check from Step 3:

| Hardware | Model | Size | Download |
|----------|-------|------|----------|
| No NVIDIA (pure CPU) | `ggml-small-q8_0.bin` | ~264 MB | https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin |
| NVIDIA GPU | `ggml-large-v3-turbo-q5_0.bin` | ~574 MB | https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin |

Save the file into this folder's `models/` directory.

`config.json` already points at the small model. Then, per the Step 3 check:

- **No NVIDIA**: set `"whisper_threads"` to the physical core count the check printed, e.g. `"whisper_threads": 8,` (0 keeps whisper's default of 4 threads; using all cores = faster transcription).
- **NVIDIA**: point `whisper_model` at the large model:
  ```json
  "whisper_model": "models/ggml-large-v3-turbo-q5_0.bin",
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
- The first launch takes a few seconds (model loading) and opens a welcome page in your browser.
- Double-clicking again while the app is running opens the settings page (http://127.0.0.1:8091).

### Give it the real icon (optional, recommended)

The `.vbs` launcher shows a generic script icon. Create a desktop shortcut with the Orac Voice icon (PowerShell, from this folder):

```powershell
$sh  = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut("$env:USERPROFILE\Desktop\Orac Voice.lnk")
$lnk.TargetPath       = "$PWD\Orac Voice.vbs"
$lnk.IconLocation     = "$PWD\assets\OracVoice.ico"
$lnk.WorkingDirectory = "$PWD"
$lnk.Save()
```

Launch from that shortcut from now on.

## Step 7: test

1. Open Notepad.
2. Hold the **Right Ctrl** key, say "testing, testing, one, two, three", and release.
3. A black pill appears at the bottom center while you record, and the text pastes itself.

Voice-free alternative test (terminal, in this folder):
```
python flow.py --test test-audio.wav
```
It should print the RAW and CLEAN text lines.

## Cloud mode (Groq, optional)

Run transcription + cleanup on Groq's free API instead of the local engines. On a CPU-only laptop a 60 s dictation drops from ~20-30 s of waiting to ~1-2 s, with better accuracy (large-v3-turbo instead of small). Trade-off: **your voice audio is sent to Groq** (they don't train on API data, but it does leave your machine).

1. Create a free API key (no credit card) at https://console.groq.com/keys
2. Save it in a file named `groq_key.txt` in this folder (next to `flow.py`). It is gitignored: never commit it, never put it in `config.json`. Alternative: set the `GROQ_API_KEY` environment variable.
3. In `config.json`: `"provider": "local"` → `"provider": "groq"`
4. Restart the app (Quit button in settings, then double-click the .vbs).

With `provider: groq`, Steps 3, 4 and 5 (whisper.cpp, model, Ollama) are unused: skip them on a fresh install, or uninstall Ollama to free ~3 GB on an existing one. Free tier limits (8 h of audio and 1,000 cleanups per day) are far beyond real dictation use. If Groq ever retires a model (`model_decommissioned`, HTTP 400), update `groq_stt_model` / `groq_chat_model` in `config.json` per https://console.groq.com/docs/deprecations

## Daily use

| Action | How |
|--------|-----|
| Dictate | Hold Right Ctrl while speaking, release when done |
| Hands-free | Double-tap Right Ctrl; one more tap to finish |
| Cancel | Escape |
| Settings, history and dictionary | Double-click Orac Voice.vbs (or http://127.0.0.1:8091) |
| Change the dictation key | In settings, click the key button and press the new key |
| Custom words (acronyms, brands) | Settings → Dictionary: type the word as it should be written, click Record and say it once |
| Quit the app | Red "Quit" button at the top right of the settings page |

## Start automatically on boot (optional)

1. `Win + R`, type `shell:startup`, Enter.
2. Copy the "Orac Voice" desktop shortcut from Step 6 into the folder that opened (or right-click `Orac Voice.vbs` → Show more options → Create shortcut and move that in).

## Troubleshooting

- **Nothing happens when dictating**: check the log at `.tmp\orac.log` inside this folder.
- **"pythonw is not recognized"**: Python is not on PATH. Reinstall checking "Add python.exe to PATH".
- **Text doesn't paste but the "done" sound plays**: the text is ALWAYS in the clipboard; paste manually with Ctrl+V. Happens in apps running as administrator.
- **Whisper is slow**: check config.json: `whisper_model` must point at the small model (large is only viable with NVIDIA + cublas) and `whisper_threads` should equal your physical cores (Step 3 check). If it is still too slow, switch to Cloud mode (Groq) above: on CPU-only machines it is the biggest speedup available. If the small model mishears a specific word, teach it in Settings → Dictionary instead of changing models.
- **Restart the app**: red "Quit" button at the top right of the settings page, then double-click the .vbs again. (Plan B: Task Manager → end `pythonw.exe`; in that case whisper-server may stay running, which is fine, the next launch reuses it.)
- **The Alt key opens the app's menu when released**: that's Windows behavior with Alt; use Right Ctrl or a Shift key as the dictation key.

## Notes for AI agents

- Run the Step 3 PowerShell hardware check FIRST and follow its verdict. Never install the large model on a machine without NVIDIA: CPU-only large ≈ 60 s per dictation, regardless of RAM. On CPU machines set `whisper_threads` in config.json to the physical core count, or recommend Cloud mode (Groq), which makes local engine speed irrelevant.
- Cloud mode: `"provider": "groq"` in config.json + the key in `groq_key.txt` next to flow.py (gitignored: NEVER put the key in config.json or in a commit). whisper.cpp, the model and Ollama are then not needed. Verification: `python flow.py --test test-audio.wav` passes with the key present, and exits with a clear "no API key" message without it. The engine can also be switched live from the settings page (Engine row).
- Desktop/startup shortcuts should point at `Orac Voice.vbs` with `assets\OracVoice.ico` as IconLocation (see Step 6): the raw .vbs shows a generic icon.
- This folder is self-contained: `flow.py` (daemon), `pill.py` (visual indicator), `settings.html` (UI served at 127.0.0.1:8091), `config.json` (configuration), `fonts/` (offline).
- The port 8091 bind is the single-instance lock: if `GET /api/state` answers, the app is already running.
- Local API: `GET /api/state` (config + mics + history + dictionary), `POST /api/config`, `POST /api/history/clear`, `POST /api/dict/record`, `POST /api/dict/delete`, `POST /api/quit` (clean shutdown).
- Install verification, in order: (1) `python --version` ≥ 3.11, (2) `pip show sounddevice pynput`, (3) `whisper-bin\whisper-server.exe` exists, (4) the `.bin` in `models\` exists and matches `whisper_model` in config.json, (5) `ollama list` includes `llama3.2:3b`, (6) `python flow.py --test test-audio.wav` prints RAW and CLEAN, (7) launch the .vbs and `curl http://127.0.0.1:8091/api/state` returns JSON.
- Do not edit `system_prompt` in config.json unless explicitly asked: it is calibrated for mixed Chilean Spanish + English.
