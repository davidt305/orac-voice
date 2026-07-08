#!/usr/bin/env python3
"""Orac Voice for Windows: local Wispr Flow clone.

Hold Right Ctrl to dictate; release and the clean text is pasted at your cursor.
Double-tap = hands-free (one more tap stops it).
Pipeline: mic -> whisper-server (local) -> Ollama (cleanup) -> clipboard + Ctrl+V.

Usage:
  pythonw flow.py             # live daemon (or double-click "Orac Voice.vbs")
  python flow.py --test x.wav # headless pipeline over a WAV, no hotkey/mic
"""
import array
import atexit
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

VERSION = "1.5"
UI_PORT = 8091
BASE = Path(__file__).resolve().parent
CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))

# provider=groq: STT + cleanup on Groq's API (audio leaves the machine).
# The key lives in groq_key.txt (gitignored) or GROQ_API_KEY, NEVER in config.
GROQ_URL = "https://api.groq.com/openai/v1"
_kf = BASE / "groq_key.txt"


def groq_key():
    """Re-read per call: adding or rotating the key needs no restart."""
    return (_kf.read_text(encoding="utf-8").strip() if _kf.exists()
            else os.environ.get("GROQ_API_KEY", ""))


def groq_headers():
    # Cloudflare in front of Groq 403-bans Python-urllib's default User-Agent
    return {"Authorization": "Bearer " + groq_key(),
            "User-Agent": "OracVoice/" + VERSION}

if sys.stdout is None or sys.stderr is None:  # pythonw: no console, log to a file
    (BASE / ".tmp").mkdir(exist_ok=True)
    sys.stdout = sys.stderr = open(BASE / ".tmp" / "orac.log", "a",
                                   buffering=1, encoding="utf-8")

# bindable modifier keys: pynput name -> label.
# Left out on purpose: Win keys (they open the Start menu on release) and Left
# Ctrl (AltGr keyboards synthesize a phantom Left Ctrl on every AltGr).
CAPTURE_KEYS = {
    "ctrl_r": "Right Ctrl",
    "alt_l": "Left Alt", "alt_r": "Right Alt", "alt_gr": "AltGr",
    "shift_r": "Right Shift", "shift_l": "Left Shift",
}
# Mac or corrupt config -> default Right Ctrl
if not isinstance(CFG.get("hotkey"), dict) \
        or CFG["hotkey"].get("key") not in CAPTURE_KEYS:
    CFG["hotkey"] = {"key": "ctrl_r", "label": "Right Ctrl"}
_watch = [CFG["hotkey"]["key"]]

# capture mode: the page asks "detect the next key the user presses"
_capture = {"active": False, "result": None}
_watched_down = False


def _on_key(name, down, on_down, on_up):
    """Keyboard logic, split from pynput so it can be tested without Windows."""
    global _watched_down
    if _capture["active"]:
        if down and name in CAPTURE_KEYS:
            _capture["active"] = False
            _capture["result"] = {"key": name, "label": CAPTURE_KEYS[name]}
        return  # while capturing, don't dictate
    if name == _watch[0]:
        if down and not _watched_down:
            _watched_down = True  # Windows repeats keydown while the key is held
            on_down()
        elif not down and _watched_down:
            _watched_down = False
            on_up()

# ---------------------------------------------------------------- platform
# The 4 functions in this section are the ONLY difference from the Mac version.

def setup_hotkey_listener(on_down, on_up, on_escape=None):
    """Global keyboard hook via pynput (runs in its own thread).
    No injected-event filter: RDP, PowerToys and on-screen keyboards need
    injected events, and our synthetic Ctrl+V only touches Left Ctrl + V,
    which are not in CAPTURE_KEYS, so it can't self-trigger a dictation."""
    from pynput import keyboard

    def on_press(key):
        if on_escape and key == keyboard.Key.esc:
            on_escape()
            return
        name = getattr(key, "name", None)
        if name:
            _on_key(name, True, on_down, on_up)

    def on_release(key):
        name = getattr(key, "name", None)
        if name:
            _on_key(name, False, on_down, on_up)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()
    return listener


def set_clipboard(text):
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    k.GlobalAlloc.restype = wintypes.HGLOBAL   # else 64-bit truncates the handle
    k.GlobalLock.restype = wintypes.LPVOID
    k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    for _ in range(10):  # another process may be holding the clipboard
        if u.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("clipboard held by another process")
    try:
        u.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        h = k.GlobalAlloc(0x0042, len(data))  # GMEM_MOVEABLE | GMEM_ZEROINIT
        p = k.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        u.SetClipboardData(13, h)  # CF_UNICODETEXT; the system now owns h
    finally:
        u.CloseClipboard()


def press_paste():
    """Synthetic Ctrl+V. If no text field is focused nothing happens,
    but the text is already in the clipboard (that IS the fallback)."""
    from pynput.keyboard import Controller, Key
    kbd = Controller()
    with kbd.pressed(Key.ctrl):
        kbd.press("v")
        kbd.release("v")


def play_sound(alias):
    """Windows system sound by alias ("SystemAsterisk", etc.)."""
    if alias:
        import winsound
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)


def _quit_app():
    """Clean quit from the settings page (Quit button)."""
    if _whisper_proc:
        _whisper_proc.terminate()
    if PILL:
        PILL.quit()  # closes the tkinter mainloop; the process exits on its own
    else:
        import os
        os._exit(0)


def do_uninstall():
    """Full self-uninstall: shortcuts and this folder. ponytail: a detached cmd
    deletes the folder after we exit, since Windows locks a live process's
    folder. Recoverable by re-cloning from GitHub."""
    if _whisper_proc:
        _whisper_proc.terminate()
    for lnk in [Path(os.environ["USERPROFILE"]) / "Desktop" / "Orac Voice.lnk",
                Path(os.environ.get("APPDATA", "")) /
                "Microsoft/Windows/Start Menu/Programs/Startup/Orac Voice.lnk"]:
        try:
            lnk.unlink()
        except OSError:
            pass
    # ping as a ~2s delay: timeout.exe needs a console, which a detached
    # process doesn't have; ping waits reliably with no console.
    subprocess.Popen(["cmd", "/c", f'ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{BASE}"'],
                     creationflags=0x00000008)  # DETACHED_PROCESS


# ---------------------------------------------------------------- self-update
# The install is a git clone, so updating = move to the latest tagged release and
# relaunch. User data (history.jsonl, dictionary.json, groq_key.txt) is gitignored
# so a reset --hard never touches it. Pull-based: the app asks GitHub.
REPO = "davidt305/orac-voice"
_update_cache = {"at": -1e9, "info": None}  # 1 check/launch + every ~6h


def _version_tuple(v):
    """'v1.4' / '1.4.0' -> (1, 4, 0). Numeric compare so v1.10 > v1.9."""
    return tuple(int(n) for n in re.findall(r"\d+", v or "")) or (0,)


def check_update(force=False):
    """Latest tagged GitHub release vs VERSION. Cached ~6h (GitHub allows 60
    req/h unauthenticated). Returns {available, version, notes, url}; on any
    error (offline, rate-limited) returns available=False."""
    now = time.monotonic()
    if not force and _update_cache["info"] and now - _update_cache["at"] < 6 * 3600:
        return _update_cache["info"]
    info = {"available": False, "version": VERSION, "notes": "", "url": ""}
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "OracVoice/" + VERSION})
        with urllib.request.urlopen(req, timeout=6) as r:
            rel = json.loads(r.read())
        tag = rel.get("tag_name", "")
        if tag and _version_tuple(tag) > _version_tuple(VERSION):
            info = {"available": True, "version": tag.lstrip("v"),
                    "notes": (rel.get("name") or "").strip(),
                    "url": rel.get("html_url", "")}
    except Exception:
        pass
    _update_cache.update(at=now, info=info)
    return info


def apply_update():
    """Move this clone to the latest tag and reinstall deps if they changed.
    Raises on git failure (the endpoint reports it). Relaunch is separate."""
    tag = "v" + check_update(force=True)["version"]
    git = ["git", "-C", str(BASE)]
    reqs = BASE / "requirements.txt"
    before = reqs.read_bytes() if reqs.exists() else b""
    subprocess.run(git + ["fetch", "--tags", "--force"], check=True,
                   capture_output=True, timeout=120)
    subprocess.run(git + ["reset", "--hard", tag], check=True,
                   capture_output=True, timeout=60)
    if reqs.exists() and reqs.read_bytes() != before:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(reqs)],
                       check=False, timeout=600)


def _relaunch():
    """Quit and reopen once the port is free (single-instance lock). ping is a
    ~2s delay that needs no console (a detached process has none)."""
    if _whisper_proc:
        _whisper_proc.terminate()
    subprocess.Popen(
        ["cmd", "/c", f'ping 127.0.0.1 -n 3 >nul & '
         f'start "" "{sys.executable}" "{BASE / "flow.py"}"'],
        creationflags=0x00000008)  # DETACHED_PROCESS
    _quit_app()


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def notify(title, message):
    """Best-effort Windows toast via PowerShell/WinRT (no extra dependency)."""
    ps = ("[Windows.UI.Notifications.ToastNotificationManager,Windows.UI."
          "Notifications,ContentType=WindowsRuntime]>$null;"
          "$x=[Windows.UI.Notifications.ToastNotificationManager]::"
          "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::"
          "ToastText02);$t=$x.GetElementsByTagName('text');"
          f"$t.Item(0).AppendChild($x.CreateTextNode({_ps_quote(title)}))>$null;"
          f"$t.Item(1).AppendChild($x.CreateTextNode({_ps_quote(message)}))>$null;"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier"
          "('com.davidt.oracvoice').Show("
          "[Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                         creationflags=0x08000000)  # CREATE_NO_WINDOW
    except Exception:
        pass


def notify_if_update():
    """On launch: if a newer release exists, nudge with a toast (the Settings
    banner is the primary surface)."""
    info = check_update()
    if info["available"]:
        notify("Orac Voice update available",
               f"v{info['version']} is ready. Open Settings to update.")


# ---------------------------------------------------------------- audio
SAMPLE_RATE = 16000
_audio_buf = []
_recording = False
PILL = None  # pill.Pill instance in live mode; None in --test


_level_smooth = 0.05


def _audio_cb(indata, frames, t, status):
    global _level_smooth
    if _dict_rec["active"]:
        _dict_rec["buf"].append(bytes(indata))
    if _recording:
        chunk = bytes(indata)
        _audio_buf.append(chunk)
        if PILL:
            a = array.array("h", chunk)
            peak = max(abs(s) for s in a[::16]) / 32768.0
            # exponential smoothing: the waveform breathes, doesn't jump
            _level_smooth = 0.65 * _level_smooth + 0.35 * min(1.0, peak * 1.8)
            PILL.push_level(_level_smooth)


def warm_ollama():
    """Preloads the Ollama model without generating anything (messages=[] = preload).
    Fires when you press Fn: while you speak, the model is already loading."""
    if CFG.get("provider") == "groq" or not CFG.get("cleaner_enabled", True):
        return  # nothing to preload: cloud mode, or cleaner off
    def _ping():
        try:
            body = json.dumps({"model": CFG["ollama_model"], "messages": [],
                               "keep_alive": "10m"}).encode()
            req = urllib.request.Request(CFG["ollama_url"], data=body,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30)
        except Exception:
            pass  # if it fails, clean() does its own fallback
    threading.Thread(target=_ping, daemon=True).start()


def start_recording():
    global _recording
    _audio_buf.clear()
    _recording = True
    play_sound(CFG["sound_start"])
    if PILL:
        PILL.show_recording()
    warm_ollama()


def stop_recording():
    """-> (raw_bytes, rate, duration_s). Cheap on purpose: runs in the
    hotkey callback; the heavy resample/encode goes to the worker (finish)."""
    global _recording
    _recording = False
    play_sound(CFG["sound_stop"])
    raw = b"".join(_audio_buf)
    return raw, _capture_rate, len(raw) / (_capture_rate * 2)  # int16 mono


def _encode_wav16k(raw, rate):
    """int16 mono at any rate -> 16kHz WAV bytes for whisper."""
    raw = _resample_16k(raw, rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(raw)
    return buf.getvalue()


def _is_silence(raw):
    """True if the audio has no voice (muted/unplugged mic). Without this gate
    whisper hallucinates phrases like "Thank you." over silence."""
    samples = array.array("h", raw)
    return not samples or max(abs(s) for s in samples) < 500


# ---------------------------------------------------------------- HTTP (stdlib)
def multipart_post(url, fields, file_bytes, timeout, headers=None):
    boundary = "----flowlocal" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f"name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                 f"name=\"file\"; filename=\"audio.wav\"\r\n"
                 f"Content-Type: audio/wav\r\n\r\n".encode())
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(url, data=b"".join(parts), headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def transcribe(wav_bytes):
    """-> (raw_text, ms)"""
    if (CFG.get("provider") != "groq"
            and CFG.get("stt_engine", "whisper") == "parakeet"):
        return transcribe_parakeet(wav_bytes)
    t0 = time.monotonic()
    fields = {"response_format": "json", "temperature": "0.0"}
    url, timeout, headers = CFG["whisper_url"], 120, None
    if CFG.get("provider") == "groq":
        url = GROQ_URL + "/audio/transcriptions"
        fields["model"] = CFG.get("groq_stt_model", "whisper-large-v3-turbo")
        headers = groq_headers()
        timeout = 30  # cloud: fail fast instead of holding PROCESSING for 2 min
        if CFG["language"] != "auto":  # Groq: omitted language = autodetect
            fields["language"] = CFG["language"]
    else:
        fields["language"] = CFG["language"]  # whisper.cpp accepts "auto"
    # initial_prompt: in Auto, a bilingual seed anchors whisper to transcribe
    # each language as-is; without it, with real voice it detects ONE language
    # for the whole window and TRANSLATES the rest. Dictionary vocab is added.
    parts = []
    if CFG["language"] == "auto":
        parts.append("Ya, perfecto, entonces revisamos el presupuesto del "
                     "cliente, so we need to check the budget with the Sales "
                     "Team, y confirmamos la reunión del martes, ok let's "
                     "begin.")
    parts += [e["written"] for e in dict_load()]
    if parts:
        fields["prompt"] = " ".join(parts)
    resp = multipart_post(url, fields, wav_bytes, timeout=timeout, headers=headers)
    text = " ".join(resp.get("text", "").split())  # whisper puts \n in the text
    return text, int((time.monotonic() - t0) * 1000)


# ------------------------------------------------------------ parakeet (local STT)
# stt_engine=parakeet: NVIDIA Parakeet tdt-0.6b-v3 int8 via onnx-asr, ~10x
# faster than whisper on CPU. It picks ONE language per clip and TRANSLATES
# the rest (no prompt to anchor it), so the clip is split at pauses and each
# chunk is recognized on its own: sentence-level ES/EN mixing survives,
# mid-sentence mixing may lose words (that's whisper's strength).
_parakeet = None
_parakeet_lock = threading.Lock()


def ensure_parakeet():
    """Load the model (first time downloads ~700MB to the HF cache). Blocks;
    call from a thread on hot-switch. Raises if onnx-asr is not installed."""
    global _parakeet
    with _parakeet_lock:
        if _parakeet is None:
            import onnx_asr  # .venv/bin/pip install "onnx-asr[cpu,hub]"
            print("Loading Parakeet (first time downloads ~700MB)...")
            _parakeet = onnx_asr.load_model(
                "nemo-parakeet-tdt-0.6b-v3", quantization="int8",
                providers=["CPUExecutionProvider"])  # CPU beats CoreML here
            print("Parakeet ready.")
    return _parakeet


def _split_on_pauses(raw, rate, pause_s=0.45):
    """int16 mono -> chunks split at silences >= pause_s. 30ms windows,
    150ms padding each side, threshold relative to the clip's own peak so a
    noisy mic floor doesn't mask the pauses. [] if nothing voiced."""
    samples = array.array("h", raw)
    win = int(rate * 0.03)
    n = len(samples) // win
    need, pad = int(pause_s / 0.03), 5  # pad: 5 windows = 150ms
    peaks = [max(map(abs, samples[i * win:(i + 1) * win]), default=0)
             for i in range(n)]
    thresh = max(500, max(peaks, default=0) // 20)
    chunks, start, gap = [], None, 0
    for i in range(n):
        if peaks[i] >= thresh:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= need:
                chunks.append((start, i - gap + 1))
                start, gap = None, 0
    if start is not None:
        chunks.append((start, n))
    return [samples[max(0, a - pad) * win:min(n, b + pad) * win].tobytes()
            for a, b in chunks]


def transcribe_parakeet(wav_bytes):
    """-> (raw_text, ms). Same contract as transcribe()."""
    import numpy as np  # onnx-asr dependency, present when parakeet works
    t0 = time.monotonic()
    model = ensure_parakeet()
    with wave.open(io.BytesIO(wav_bytes)) as w:
        raw = w.readframes(w.getnframes())
    parts = []
    for chunk in _split_on_pauses(raw, SAMPLE_RATE) or [raw]:
        wf = np.frombuffer(chunk, np.int16).astype(np.float32) / 32768.0
        parts.append(model.recognize(wf, sample_rate=SAMPLE_RATE))
    text = " ".join(" ".join(p.split()) for p in parts if p).strip()
    return text, int((time.monotonic() - t0) * 1000)


def ensure_stt():
    """Startup/test: bring up whatever the configured STT engine needs."""
    if (CFG.get("provider") != "groq"
            and CFG.get("stt_engine", "whisper") == "parakeet"):
        try:
            ensure_parakeet()
        except ImportError:
            sys.exit("stt_engine=parakeet but onnx-asr is missing: "
                     ".venv/bin/pip install \"onnx-asr[cpu,hub]\"")
    else:
        ensure_whisper()


def _norm_words(s):
    """Comparable words: no punctuation at the edges, lowercased.
    Shared by the cleaner guard and the dictionary keys."""
    return [w.strip(".,;:¿?¡!\"'()").lower() for w in s.split()]


def _rewrote(raw, text):
    """True if the cleaner added words that were not in the raw text.
    Its contract is to only DELETE filler words: too many new words means it
    translated, paraphrased or replied like a chatbot (llama3.2:3b tends to
    unify bilingual dictations into the first sentence's language)."""
    raw_words = set(_norm_words(raw))
    out = [w for w in _norm_words(text) if w]
    if not out:
        return True
    # zero tolerance: even ONE new word means translation/rewriting
    # (with a percentage threshold, "operations"->"operaciones" got through)
    return any(w not in raw_words for w in out)


def clean(raw):
    """-> (clean_text, ms, fell_back). Never raises: if the cleaner fails, returns raw."""
    if not CFG.get("cleaner_enabled", True):
        return raw, 0, False  # cleaner off: paste exactly what the engine heard
    t0 = time.monotonic()
    try:
        # Input:/Output: mirrors the pattern of the system prompt examples:
        # the model completes the transformation instead of "replying" to it
        messages = [{"role": "system", "content": CFG["system_prompt"]},
                    {"role": "user", "content": f"Input: {raw}\nOutput:"}]
        if CFG.get("provider") == "groq":
            body = json.dumps({
                "model": CFG.get("groq_chat_model", "llama-3.3-70b-versatile"),
                "messages": messages,
                "temperature": 0,
            }).encode()
            req = urllib.request.Request(
                GROQ_URL + "/chat/completions", data=body,
                headers={"Content-Type": "application/json", **groq_headers()})
            with urllib.request.urlopen(req, timeout=CFG["ollama_timeout_s"]) as r:
                text = json.loads(
                    r.read())["choices"][0]["message"]["content"].strip()
        else:
            body = json.dumps({
                "model": CFG["ollama_model"],
                "messages": messages,
                "stream": False,
                "keep_alive": "10m",
                "options": {"temperature": 0},
            }).encode()
            req = urllib.request.Request(CFG["ollama_url"], data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=CFG["ollama_timeout_s"]) as r:
                text = json.loads(r.read())["message"]["content"].strip()
        if text:
            if _rewrote(raw, text):
                print("  cleaner rewrote (new words) -> using raw text")
                return raw, int((time.monotonic() - t0) * 1000), True
            return text, int((time.monotonic() - t0) * 1000), False
    except Exception as e:
        print(f"  cleaner failed ({e!r}) -> using raw text")
    return raw, int((time.monotonic() - t0) * 1000), True


# ---------------------------------------------------------------- history
HISTORY_FILE = BASE / "history.jsonl"
_hist_lock = threading.Lock()


def history_append(text):
    with _hist_lock:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "text": text},
                               ensure_ascii=False) + "\n")
        items = _history_all()
        if len(items) > 1000:  # ponytail: fixed cap; the 5s poll can't grow forever
            HISTORY_FILE.write_text(
                "".join(json.dumps(i, ensure_ascii=False) + "\n"
                        for i in items[-500:]), encoding="utf-8")


def _history_all():
    if not HISTORY_FILE.exists():
        return []
    with open(HISTORY_FILE, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def history_read(limit=100):
    with _hist_lock:
        return list(reversed(_history_all()))[:limit]  # newest first


def history_delete(ts):
    with _hist_lock:
        items = [i for i in _history_all() if i["ts"] != ts]
        HISTORY_FILE.write_text(
            "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items),
            encoding="utf-8")


def history_clear():
    with _hist_lock:
        HISTORY_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------- dictionary
# Custom vocabulary: {"written": "n8n", "spoken": ["ene ocho ene", ...]}.
# The key is what whisper HEARS (recorded once from the settings page);
# the replacement is deterministic, without going through the LLM.
DICT_FILE = BASE / "dictionary.json"
_dict_rec = {"active": False, "buf": []}


def dict_load():
    if not DICT_FILE.exists():
        return []
    return json.loads(DICT_FILE.read_text(encoding="utf-8"))


def dict_save(entries):
    DICT_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")


def _norm_spoken(s):
    return " ".join(_norm_words(s))


def dict_record_word():
    """Records ~2.5s from the mic, transcribes, returns the normalized spoken key."""
    _dict_rec["buf"] = []
    _dict_rec["active"] = True
    play_sound(CFG["sound_start"])
    time.sleep(2.5)  # ponytail: fixed window; enough for a word or an acronym
    _dict_rec["active"] = False
    play_sound(CFG["sound_stop"])
    raw = b"".join(_dict_rec["buf"])
    _dict_rec["buf"] = []
    if _is_silence(raw):
        return ""
    spoken, _ = transcribe(_encode_wav16k(raw, _capture_rate))
    return _norm_spoken(spoken)


def apply_dictionary(text):
    """Spoken variant -> written form, case-insensitive. Tolerates the
    punctuation whisper puts between words ("ene, ocho, ene")."""
    for e in dict_load():
        for v in sorted(e["spoken"], key=len, reverse=True):
            pat = r"\b" + r"[,.]*\s+".join(re.escape(w) for w in v.split()) + r"\b"
            text = re.sub(pat, lambda m: e["written"], text, flags=re.IGNORECASE)
    return text


# ---------------------------------------------------------------- audio stream
_sd = None      # sounddevice module (imported only in live mode)
_stream = None


_capture_rate = SAMPLE_RATE  # actual stream rate (16k, or the mic's native)


def open_stream():
    """(Re)opens the mic stream per CFG['mic'] (None = system default).
    If the mic rejects 16kHz (typical 48k USB like the Shure MV7+), opens at
    its native rate and stop_recording() resamples to 16k for Whisper."""
    global _stream, _capture_rate
    if _sd is None:
        return  # --test / logic tests: no live audio
    if _stream:
        _stream.stop()
        _stream.close()
        _stream = None
    if not CFG.get("mic_enabled", True):
        return  # privacy toggle: mic released, no "in use" indicator
    dev = None
    if CFG.get("mic"):
        for i, d in enumerate(_sd.query_devices()):
            if d["max_input_channels"] > 0 and d["name"] == CFG["mic"]:
                dev = i
                break
    # ponytail: stream always open (persistent orange dot) : avoids clipping
    # the first word; switch to open/close per dictation if it bothers you.
    try:
        _stream = _sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1,
                                     dtype="int16", callback=_audio_cb, device=dev)
        _capture_rate = SAMPLE_RATE
    except Exception:
        info = _sd.query_devices(dev, "input") if dev is not None \
            else _sd.query_devices(kind="input")
        native = int(info["default_samplerate"])
        _stream = _sd.RawInputStream(samplerate=native, channels=1,
                                     dtype="int16", callback=_audio_cb, device=dev)
        _capture_rate = native
        log(f"mic at {native}Hz (16k not accepted), resampling enabled")
    _stream.start()


def _resample_16k(raw, rate):
    """Linear int16 mono resample -> 16kHz. Good enough for voice + Whisper."""
    if rate == SAMPLE_RATE:
        return raw
    src = array.array("h", raw)
    n_out = int(len(src) * SAMPLE_RATE / rate)
    out = array.array("h", bytes(2 * n_out))
    step = rate / float(SAMPLE_RATE)
    for i in range(n_out):
        pos = i * step
        j = int(pos)
        frac = pos - j
        a = src[j]
        b = src[j + 1] if j + 1 < len(src) else a
        out[i] = int(a + (b - a) * frac)
    return out.tobytes()


# ---------------------------------------------------------------- UI server
def save_config():
    (BASE / "config.json").write_text(
        json.dumps(CFG, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def start_ui_server():
    """Settings page on localhost:8091. The bind IS the single-instance lock."""
    import http.server
    import socketserver

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(
                body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/":  # "/?welcome" also lands here
                self._send(200, (BASE / "settings.html").read_bytes(),
                           "text/html; charset=utf-8")
            elif self.path.startswith("/fonts/"):
                f = BASE / "fonts" / Path(self.path).name  # .name blocks traversal
                if f.is_file():
                    self._send(200, f.read_bytes(), "font/ttf")
                else:
                    self._send(404, {"error": "font not found"})
            elif self.path == "/api/state":
                mics = sorted({d["name"] for d in _sd.query_devices()
                               if d["max_input_channels"] > 0}) if _sd else []
                if CFG.get("provider") == "groq":
                    stt = "Groq · " + CFG.get("groq_stt_model",
                                              "whisper-large-v3-turbo")
                    llm = "Groq · " + CFG.get("groq_chat_model",
                                              "llama-3.3-70b-versatile")
                elif CFG.get("stt_engine", "whisper") == "parakeet":
                    stt = "Parakeet · tdt-0.6b-v3 int8"
                    llm = "Ollama · " + CFG["ollama_model"]
                else:
                    stt = "Whisper · " + Path(
                        CFG["whisper_model"]).stem.replace("ggml-", "")
                    llm = "Ollama · " + CFG["ollama_model"]
                engine = ("groq" if CFG.get("provider") == "groq"
                          else CFG.get("stt_engine", "whisper"))
                if not CFG.get("cleaner_enabled", True):
                    llm = "Cleaner off"
                self._send(200, {
                    "config": {"hotkey": CFG["hotkey"],
                               "language": CFG["language"],
                               "mic": CFG.get("mic") or "",
                               "provider": CFG.get("provider", "local"),
                               "engine": engine,
                               "cleaner_enabled": CFG.get("cleaner_enabled",
                                                          True),
                               "mic_enabled": CFG.get("mic_enabled", True)},
                    "about": {"version": VERSION, "stt": stt, "llm": llm},
                    "mics": mics, "history": history_read(),
                    "dictionary": dict_load()})
            elif self.path == "/api/capture":
                self._send(200, _capture)
            elif self.path == "/api/update/check":
                self._send(200, check_update())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/capture/start":
                if _state != IDLE:
                    # capturing while dictating would swallow the release of
                    # the held key and the mic would keep recording forever
                    return self._send(409, "Finish dictating first".encode(),
                                      "text/plain; charset=utf-8")
                _capture["active"] = True
                _capture["result"] = None
                return self._send(200, {"ok": True})
            if self.path == "/api/capture/cancel":
                _capture["active"] = False
                return self._send(200, {"ok": True})
            if self.path == "/api/config":
                hk = body.get("hotkey")
                if isinstance(hk, dict) and hk.get("key") in CAPTURE_KEYS:
                    CFG["hotkey"] = {"key": hk["key"],
                                     "label": CAPTURE_KEYS[hk["key"]]}
                    _watch[0] = hk["key"]
                if body.get("language") in ("auto", "es", "en"):
                    CFG["language"] = body["language"]
                if body.get("provider") in ("local", "groq"):
                    if body["provider"] == "groq" and not groq_key():
                        return self._send(
                            400, ("No Groq API key: put it in groq_key.txt "
                                  "next to flow.py").encode(),
                            "text/plain; charset=utf-8")
                    CFG["provider"] = body["provider"]
                    if body["provider"] == "local":
                        # ponytail: fire-and-forget; if the local server can't
                        # start, the next dictation errors and the log says why
                        threading.Thread(target=ensure_whisper,
                                         daemon=True).start()
                if body.get("engine") in ("whisper", "parakeet", "groq"):
                    eng = body["engine"]
                    if eng == "groq":
                        if not groq_key():
                            return self._send(
                                400, ("No Groq API key: put it in groq_key.txt "
                                      "next to flow.py").encode(),
                                "text/plain; charset=utf-8")
                        CFG["provider"] = "groq"
                    else:
                        if eng == "parakeet":
                            try:
                                import onnx_asr  # noqa: F401
                            except ImportError:
                                return self._send(
                                    400, ("Parakeet needs onnx-asr: pip "
                                          "install \"onnx-asr[cpu,hub]\" in "
                                          "the app venv").encode(),
                                    "text/plain; charset=utf-8")
                        CFG["provider"] = "local"
                        CFG["stt_engine"] = eng
                        # fire-and-forget like provider: errors surface on the
                        # next dictation (parakeet's 1st load downloads ~700MB)
                        threading.Thread(
                            target=(ensure_parakeet if eng == "parakeet"
                                    else ensure_whisper),
                            daemon=True).start()
                if body.get("cleaner_enabled") in ("on", "off"):
                    CFG["cleaner_enabled"] = body["cleaner_enabled"] == "on"
                if body.get("mic_enabled") in ("on", "off"):
                    if _state != IDLE:
                        return self._send(409, "Finish dictating first".encode(),
                                          "text/plain; charset=utf-8")
                    CFG["mic_enabled"] = body["mic_enabled"] == "on"
                    open_stream()
                if "mic" in body:
                    if _state != IDLE:
                        return self._send(409, "Finish dictating first".encode(),
                                          "text/plain; charset=utf-8")
                    prev = CFG.get("mic")
                    CFG["mic"] = body["mic"] or None
                    try:
                        open_stream()
                    except Exception as e:
                        CFG["mic"] = prev
                        open_stream()
                        return self._send(400, f"That mic failed: {e}".encode(),
                                          "text/plain; charset=utf-8")
                save_config()
                self._send(200, {"ok": True})
            elif self.path == "/api/dict/record":
                written = (body.get("written") or "").strip()
                if not written:
                    return self._send(400, "Type the word first".encode(),
                                      "text/plain; charset=utf-8")
                if _state != IDLE or _dict_rec["active"]:
                    return self._send(409, "Finish dictating first".encode(),
                                      "text/plain; charset=utf-8")
                if not CFG.get("mic_enabled", True):
                    return self._send(400, "Microphone is off".encode(),
                                      "text/plain; charset=utf-8")
                spoken = dict_record_word()
                if not spoken:
                    play_sound(CFG["sound_error"])
                    return self._send(400, "Didn't catch that, try again".encode(),
                                      "text/plain; charset=utf-8")
                entries = dict_load()
                for e in entries:
                    if e["written"] == written:
                        if spoken not in e["spoken"]:
                            e["spoken"].append(spoken)
                        break
                else:
                    entries.append({"written": written, "spoken": [spoken]})
                dict_save(entries)
                self._send(200, {"ok": True, "spoken": spoken})
            elif self.path == "/api/dict/delete":
                dict_save([e for e in dict_load()
                           if e["written"] != body.get("written")])
                self._send(200, {"ok": True})
            elif self.path == "/api/quit":
                self._send(200, {"ok": True})
                threading.Timer(0.3, _quit_app).start()
            elif self.path == "/api/uninstall":
                self._send(200, {"ok": True})
                do_uninstall()  # spawns a detached deleter, then we quit
                threading.Timer(0.4, _quit_app).start()
            elif self.path == "/api/history/delete":
                history_delete(body.get("ts"))
                self._send(200, {"ok": True})
            elif self.path == "/api/history/clear":
                history_clear()
                self._send(200, {"ok": True})
            elif self.path == "/api/update/apply":
                try:
                    apply_update()
                except Exception as e:
                    return self._send(500, {"ok": False, "error": str(e)})
                self._send(200, {"ok": True})
                threading.Timer(0.5, _relaunch).start()
            else:
                self._send(404, {"error": "not found"})

    # single instance: if something RESPONDS on 8091 it's a live Orac Voice.
    # (allow_reuse_address lets us relaunch right after closing, no TIME_WAIT)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{UI_PORT}/api/state", timeout=1)
        # second double-click with the app alive: show settings, don't die silently
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{UI_PORT}")
        sys.exit(0)
    except (urllib.error.URLError, OSError):
        pass

    class Srv(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

    try:
        srv = Srv(("127.0.0.1", UI_PORT), Handler)
    except OSError:
        sys.exit(f"Port {UI_PORT} is taken by another program; "
                 "close it or change UI_PORT in flow.py")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    marker = BASE / ".tmp" / "welcomed"
    if _sd is not None and not marker.exists():  # first live launch: welcome
        marker.parent.mkdir(exist_ok=True)
        marker.write_text("1")
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{UI_PORT}/?welcome")


# ---------------------------------------------------------------- whisper-server
_whisper_proc = None  # our child (None if the server was already running)


def ensure_whisper():
    if CFG.get("provider") == "groq":
        if not groq_key():
            sys.exit("provider=groq but no API key: put it in groq_key.txt "
                     "next to flow.py, or set GROQ_API_KEY")
        return  # cloud STT: no local server to start
    base = CFG["whisper_url"].rsplit("/", 1)[0]
    try:
        urllib.request.urlopen(base, timeout=2)
        return  # already running (started by hand) : idempotent
    except Exception:
        pass
    if not CFG["whisper_autostart"]:
        sys.exit(f"whisper-server not responding at {base} and autostart is off")
    print("Starting whisper-server...")
    bin_ = Path(CFG["whisper_server_bin"])
    if not bin_.is_absolute():
        bin_ = BASE / bin_
    global _whisper_proc
    cmd = [str(bin_), "-m", str(BASE / CFG["whisper_model"]),
           "--host", "127.0.0.1", "--port", base.rsplit(":", 1)[1]]
    if CFG.get("whisper_threads"):  # 0/absent = whisper-server default (4)
        cmd += ["-t", str(CFG["whisper_threads"])]
    _whisper_proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    atexit.register(_whisper_proc.terminate)
    # ponytail: no restart supervision : if the server dies later, the error
    # is logged on every dictation and you restart flow.py.
    for _ in range(60):
        time.sleep(1)
        try:
            urllib.request.urlopen(base, timeout=2)
            print("whisper-server ready.")
            return
        except Exception:
            continue
    sys.exit("whisper-server did not come up in 60s")


# ---------------------------------------------------------------- state machine
IDLE, RECORDING, MAYBE_HANDSFREE, HANDSFREE, PROCESSING = range(5)
_state = IDLE
_lock = threading.Lock()
_t_down = 0.0
_hf_timer = None


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} | {msg}", flush=True)


def finish(raw_audio, rate, duration):
    """Per-dictation worker. Runs in its own thread; the tap never waits
    (the heavy resample/encode of long audio lives here, not in the tap)."""
    global _state
    t0 = time.monotonic()
    try:
        if duration < CFG["min_record_s"]:
            log(f"discarded (too short: {duration:.2f}s)")
            play_sound(CFG["sound_error"])
            return
        if _is_silence(raw_audio):
            log("discarded (silence: muted mic?)")
            play_sound(CFG["sound_error"])
            return
        raw, ms_w = transcribe(_encode_wav16k(raw_audio, rate))
        if not raw:
            log("discarded (whisper heard nothing)")
            play_sound(CFG["sound_error"])
            return
        text, ms_o, fell_back = clean(raw)
        text = apply_dictionary(text)
        history_append(text)  # first: if the clipboard fails, the text survives
        set_clipboard(text)
        time.sleep(0.05)
        press_paste()
        total = time.monotonic() - t0
        tag = "FALLBACK raw" if fell_back else "ok"
        log(f"rec {duration:.1f}s | whisper {ms_w}ms | cleaner {ms_o}ms ({tag}) | "
            f"total {total:.2f}s | {len(text)} chars")
    except Exception:
        play_sound(CFG["sound_error"])
        traceback.print_exc()
    finally:
        if PILL:
            PILL.hide()
        with _lock:
            _state = IDLE  # never stay stuck in PROCESSING


def _spawn_finish():
    """Call with _lock held: stops the recording and launches the worker."""
    global _state
    raw, rate, dur = stop_recording()
    _state = PROCESSING
    if PILL:
        PILL.show_processing()
    threading.Thread(target=finish, args=(raw, rate, dur), daemon=True).start()


def _hf_window_expired():
    """Timer: the short tap wasn't a double-tap -> discard (accidental tap)."""
    global _state
    with _lock:
        if _state == MAYBE_HANDSFREE:
            stop_recording()
            log("discarded (accidental tap)")
            if PILL:
                PILL.hide()
            _state = IDLE


def cancel_dictation():
    """Pill X or Escape key: discard the recording, take no action."""
    global _state
    if _capture["active"]:
        _capture["active"] = False  # Escape also cancels the key capture
        return
    with _lock:
        if _state in (RECORDING, MAYBE_HANDSFREE, HANDSFREE):
            if _hf_timer:
                _hf_timer.cancel()
            stop_recording()
            log("cancelled (X/Esc)")
            if PILL:
                PILL.hide()
            _state = IDLE


def confirm_dictation():
    """Click on the pill's ✓: finish and process now."""
    with _lock:
        if _state in (RECORDING, MAYBE_HANDSFREE, HANDSFREE):
            if _hf_timer:
                _hf_timer.cancel()
            log("confirmed (✓), processing...")
            _spawn_finish()


def on_fn_down():
    global _state, _t_down, _hf_timer
    with _lock:
        if _state == IDLE:
            if not CFG.get("mic_enabled", True):
                log("mic is off (enable it in Settings)")
                play_sound(CFG["sound_error"])
                return
            start_recording()
            _t_down = time.monotonic()
            _state = RECORDING
            log("recording (hold)...")
        elif _state == MAYBE_HANDSFREE:
            _hf_timer.cancel()
            _state = HANDSFREE  # the recording never stopped: no audio gap
            log("hands-free ON (tap to stop)")
        elif _state == HANDSFREE:
            log("hands-free OFF, processing...")
            _spawn_finish()
        elif _state == PROCESSING:
            log("busy: still transcribing the previous one")


def on_fn_up():
    global _state, _hf_timer
    with _lock:
        if _state == RECORDING:
            held = time.monotonic() - _t_down
            if held >= CFG["double_tap_ms"] / 1000:
                log("processing...")
                _spawn_finish()
            else:
                # short tap: may start a double-tap; the recording continues
                _state = MAYBE_HANDSFREE
                _hf_timer = threading.Timer(CFG["double_tap_ms"] / 1000,
                                            _hf_window_expired)
                _hf_timer.start()
        # HANDSFREE: ignore (tail of the second tap). PROCESSING/IDLE: nothing.


# ---------------------------------------------------------------- main
def run_test(wav_path):
    """Headless pipeline over a WAV: the E2E self-check (no hotkey or mic)."""
    ensure_stt()
    wav = Path(wav_path).read_bytes()
    with wave.open(wav_path, "rb") as w:
        duration = w.getnframes() / w.getframerate()
    t0 = time.monotonic()
    raw, ms_w = transcribe(wav)
    assert raw, "whisper returned empty text"
    text, ms_o, fell_back = clean(raw)
    text = apply_dictionary(text)
    assert text, "pipeline returned empty text"
    set_clipboard(text)
    tag = "FALLBACK raw" if fell_back else "ok"
    log(f"rec {duration:.1f}s | whisper {ms_w}ms | cleaner {ms_o}ms ({tag}) | "
        f"total {time.monotonic() - t0:.2f}s | {len(text)} chars")
    print(f"RAW  : {raw}\nCLEAN: {text}\n(the clean text is in your clipboard)")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--test":
        run_test(sys.argv[2])
        return
    global _sd, PILL
    import sounddevice
    _sd = sounddevice
    import pill as pillmod
    pillmod.set_app_id()  # taskbar/notifications say "Orac Voice", not Python
    start_ui_server()  # also acts as the single-instance lock
    pillmod.Splash().run(ensure_stt)  # splash covers the slow model load, then closes
    open_stream()

    PILL = pillmod.Pill()  # visual only: Esc cancels, the key confirms
    setup_hotkey_listener(on_fn_down, on_fn_up, on_escape=cancel_dictation)
    print(f"Orac Voice ready. Hold {CFG['hotkey']['label']} to dictate; "
          f"double-tap = hands-free. Settings: http://127.0.0.1:{UI_PORT}")
    threading.Thread(target=notify_if_update, daemon=True).start()
    PILL.run()  # tkinter mainloop on the main thread


if __name__ == "__main__":
    main()
