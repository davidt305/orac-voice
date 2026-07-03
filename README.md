# Orac Voice

**Local-first, private push-to-talk dictation** for macOS and Windows. Hold a key, speak, release: clean text is pasted right where your cursor is. By default everything runs 100% local: no cloud, no subscription, your voice never leaves your machine. An optional cloud mode (`"provider": "groq"` in config.json) trades that guarantee for speed on weak hardware — see the install guides.

**Pipeline:** mic → whisper.cpp (local transcription) → Ollama llama3.2:3b (removes filler words only) → clipboard + automatic paste. With `provider: groq`, transcription and cleanup run on Groq's API instead (your audio leaves the machine; the free tier covers heavy daily dictation).

## Features

- **Push-to-talk**: hold the key (Fn on Mac, Right Ctrl on Windows, rebindable) and speak. Double-tap = hands-free mode.
- **Truly bilingual**: dictate in Spanish, English, or both in the same sentence. A bilingual seed prompt anchors Whisper to transcribe each language as spoken, and a deterministic guard guarantees the cleaner never translates a single word you said.
- **Custom dictionary**: type a term the way it should be written (e.g. `n8n`), record how you pronounce it once, and from then on it is typed exactly the way you want. Stored in `dictionary.json`.
- **Filler-word cleanup**: "um", "uh", "eh", "o sea", "ya po" disappear; every other word stays EXACTLY as spoken (calibrated for Chilean Spanish + English).
- **Local history**: settings page with per-dictation copy/delete. Lives in `history.jsonl`, never leaves your computer.
- **You never lose a dictation**: if the cleaner fails or gets creative, the raw Whisper transcript is pasted instead.

## Install

| Platform | Guide |
|---|---|
| macOS | [INSTALL-MAC.md](INSTALL-MAC.md) |
| Windows | [windows/INSTALL.md](windows/INSTALL.md) |
| An AI agent (Claude Code, etc.) installing it for you | [AGENTS.md](AGENTS.md) |

The `windows/` folder is self-contained: it can be copied alone to a PC.

## Usage

| Gesture | What it does |
|---|---|
| Hold the key + speak + release | Normal dictation |
| Double-tap | Hands-free (records without holding) |
| Tap again (in hands-free) | Stops and processes |
| Escape | Cancels the current dictation, pastes nothing |

**If nothing was pasted:** the text is always in your clipboard → paste manually.

## Settings (http://127.0.0.1:8091)

On Mac: menu bar 🎙 → Settings & History. On Windows: double-click the launcher while the app is running.

- **Dictation key**: click the button → press any modifier key → saved.
- **Microphone**: picker; USB mics that reject 16kHz are resampled automatically.
- **Microphone access**: On/Off. Off releases the mic entirely (the OS stops showing it "in use") until you turn it back on.
- **Language**: Auto / Español / English.
- **Engine**: Local (private, on-device) or Groq (cloud API, needs `groq_key.txt`). Switches live, no restart.
- **Dictionary**: your custom words (acronyms, brands, names). Type → Record → say it once.
- **History**: collapsible, per-item copy/delete, Clear all with confirmation.
- **Quit**: button at the bottom of the page.

## The log (one line per dictation)

```
14:32 | rec 3.4s | whisper 812ms | cleaner 590ms (ok) | total 1.41s | 142 chars
```

`FALLBACK raw` = the raw Whisper text was pasted (the cleaner failed or tried to rewrite). Log lives in `.tmp/orac.log`.

## Advanced config (config.json)

`provider` (`"local"` or `"groq"`), `groq_stt_model`, `groq_chat_model`, `double_tap_ms`, `min_record_s`, `ollama_timeout_s`, `system_prompt` (the cleaner's rules; if it makes a recurring mistake, add the exact case as a few-shot example). Note: the prompt's few-shot examples are intentionally in Spanish/mixed, they are the calibration for bilingual dictation.

## Architecture

```
flow.py          daemon: hotkey, audio, pipeline, settings server (:8091)
pill.py          floating pill (NSPanel on Mac / tkinter on Windows)
settings.html    settings page (served locally, offline)
config.json      configuration
make-app.sh      macOS: builds "/Applications/Orac Voice.app" (proper TCC identity)
windows/         complete, self-contained Windows port
```

The only platform-specific pieces are a handful of functions in `flow.py` (hotkey, clipboard, paste, sounds, quit). Everything else is identical on Mac and Windows.

## Known issues

- **Mac, the key does nothing** → System Settings → Privacy & Security → Input Monitoring + Accessibility for "Orac Voice" (or your terminal if running from one). Reopen the app afterwards.
- **Whisper hallucinates on silence** ("Thank you.", "Subtítulos realizados por…"): silent audio is already discarded by an energy gate; if it still happens, add `-sns --no-speech-thold 0.6` to the server launch in `ensure_whisper()`.
- **Windows, apps running as administrator**: pasting into them is not possible (a Windows limitation); the text stays in the clipboard.

## License

MIT. The bundled fonts in `fonts/` (Poppins, Inter) are licensed under the [SIL Open Font License 1.1](https://openfontlicense.org/).
