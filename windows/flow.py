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

VERSION = "1.0"
UI_PORT = 8091
BASE = Path(__file__).resolve().parent
CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))

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
def multipart_post(url, fields, file_bytes, timeout):
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
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def transcribe(wav_bytes):
    """-> (raw_text, ms)"""
    t0 = time.monotonic()
    fields = {
        "language": CFG["language"],
        "response_format": "json",
        "temperature": "0.0",
    }
    # initial_prompt: in Auto, a bilingual seed anchors whisper to transcribe
    # each language as-is; without it, with real voice it detects ONE language
    # for the whole window and TRANSLATES the rest. Dictionary vocab is added.
    parts = []
    if CFG["language"] == "auto":
        parts.append("Ya, perfecto, so we need to check el presupuesto with "
                     "the Sales Team, and then management, finance and "
                     "operations confirman la reunión del martes, ok let's "
                     "begin.")
    parts += [e["written"] for e in dict_load()]
    if parts:
        fields["prompt"] = " ".join(parts)
    resp = multipart_post(CFG["whisper_url"], fields, wav_bytes, timeout=120)
    text = " ".join(resp.get("text", "").split())  # whisper puts \n in the text
    return text, int((time.monotonic() - t0) * 1000)


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
    """-> (clean_text, ms, fell_back). Never raises: if Ollama fails, returns raw."""
    t0 = time.monotonic()
    try:
        body = json.dumps({
            "model": CFG["ollama_model"],
            # Input:/Output: mirrors the pattern of the system prompt examples:
            # the model completes the transformation instead of "replying" to it
            "messages": [{"role": "system", "content": CFG["system_prompt"]},
                         {"role": "user", "content": f"Input: {raw}\nOutput:"}],
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
                print("  ollama rewrote (new words) -> using raw text")
                return raw, int((time.monotonic() - t0) * 1000), True
            return text, int((time.monotonic() - t0) * 1000), False
    except Exception as e:
        print(f"  ollama failed ({e!r}) -> using raw text")
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
    if _stream:
        _stream.stop()
        _stream.close()
        _stream = None
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
            if self.path == "/":
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
                self._send(200, {
                    "config": {"hotkey": CFG["hotkey"],
                               "language": CFG["language"],
                               "mic": CFG.get("mic") or ""},
                    "about": {"version": VERSION,
                              "whisper": Path(CFG["whisper_model"]).stem
                              .replace("ggml-", ""),
                              "ollama": CFG["ollama_model"]},
                    "mics": mics, "history": history_read(),
                    "dictionary": dict_load()})
            elif self.path == "/api/capture":
                self._send(200, _capture)
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
            elif self.path == "/api/history/delete":
                history_delete(body.get("ts"))
                self._send(200, {"ok": True})
            elif self.path == "/api/history/clear":
                history_clear()
                self._send(200, {"ok": True})
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


# ---------------------------------------------------------------- whisper-server
_whisper_proc = None  # our child (None if the server was already running)


def ensure_whisper():
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
        log(f"rec {duration:.1f}s | whisper {ms_w}ms | ollama {ms_o}ms ({tag}) | "
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
    ensure_whisper()
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
    log(f"rec {duration:.1f}s | whisper {ms_w}ms | ollama {ms_o}ms ({tag}) | "
        f"total {time.monotonic() - t0:.2f}s | {len(text)} chars")
    print(f"RAW  : {raw}\nCLEAN: {text}\n(the clean text is in your clipboard)")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--test":
        run_test(sys.argv[2])
        return
    global _sd, PILL
    import sounddevice
    _sd = sounddevice
    start_ui_server()  # also acts as the single-instance lock
    ensure_whisper()
    open_stream()

    import pill as pillmod
    PILL = pillmod.Pill()  # visual only: Esc cancels, the key confirms
    setup_hotkey_listener(on_fn_down, on_fn_up, on_escape=cancel_dictation)
    print(f"Orac Voice ready. Hold {CFG['hotkey']['label']} to dictate; "
          f"double-tap = hands-free. Settings: http://127.0.0.1:{UI_PORT}")
    PILL.run()  # tkinter mainloop on the main thread


if __name__ == "__main__":
    main()
