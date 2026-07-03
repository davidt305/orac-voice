#!/usr/bin/env python3
"""Orac Voice para Windows: clon local de Wispr Flow.

Mantén Right Ctrl para dictar; suelta y el texto limpio se pega en el cursor.
Doble-tap = manos libres (un tap más lo detiene).
Pipeline: mic -> whisper-server (local) -> Ollama (limpieza) -> clipboard + Ctrl+V.

Uso:
  pythonw flow.py             # daemon en vivo (o doble click a "Orac Voice.vbs")
  python flow.py --test x.wav # pipeline headless sobre un WAV, sin hotkey/mic
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
CFG = json.loads((BASE / "config.json").read_text())

if sys.stdout is None or sys.stderr is None:  # pythonw: sin consola, log a archivo
    (BASE / ".tmp").mkdir(exist_ok=True)
    sys.stdout = sys.stderr = open(BASE / ".tmp" / "orac.log", "a",
                                   buffering=1, encoding="utf-8")

# teclas modificadoras bindeables: nombre pynput -> label
CAPTURE_KEYS = {
    "ctrl_r": "Right Ctrl", "ctrl_l": "Left Ctrl",
    "alt_l": "Left Alt", "alt_r": "Right Alt", "alt_gr": "AltGr",
    "shift_r": "Right Shift", "shift_l": "Left Shift",
    "cmd": "Left Win", "cmd_r": "Right Win",
}
# config de Mac o corrupto -> default Right Ctrl
if not isinstance(CFG.get("hotkey"), dict) \
        or CFG["hotkey"].get("key") not in CAPTURE_KEYS:
    CFG["hotkey"] = {"key": "ctrl_r", "label": "Right Ctrl"}
_watch = [CFG["hotkey"]["key"]]

# modo captura: la página pide "detecta la próxima tecla que apriete el usuario"
_capture = {"active": False, "result": None}
_watched_down = False


def _on_key(name, down, on_down, on_up):
    """Lógica de teclado, separada de pynput para poder testearla sin Windows."""
    global _watched_down
    if _capture["active"]:
        if down and name in CAPTURE_KEYS:
            _capture["active"] = False
            _capture["result"] = {"key": name, "label": CAPTURE_KEYS[name]}
        return  # mientras capturas, no se dicta
    if name == _watch[0]:
        if down and not _watched_down:
            _watched_down = True  # Windows repite keydown al mantener la tecla
            on_down()
        elif not down and _watched_down:
            _watched_down = False
            on_up()

# ---------------------------------------------------------------- plataforma
# Las 4 funciones de esta sección son la ÚNICA diferencia con la versión Mac.

def setup_hotkey_listener(on_down, on_up, on_escape=None):
    """Hook global de teclado vía pynput (corre en su propio thread).
    Filtra los eventos inyectados por la propia app (LLKHF_INJECTED): nuestro
    Ctrl+V sintético no debe disparar un dictado si la tecla bindeada es Ctrl."""
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

    listener = keyboard.Listener(
        on_press=on_press, on_release=on_release,
        win32_event_filter=lambda msg, data: not (data.flags & 0x10))
    listener.daemon = True
    listener.start()
    return listener


def set_clipboard(text):
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    k.GlobalAlloc.restype = wintypes.HGLOBAL   # sin esto, 64-bit trunca el handle
    k.GlobalLock.restype = wintypes.LPVOID
    k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    for _ in range(3):  # otro proceso puede tener el clipboard tomado
        if u.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("clipboard ocupado por otro proceso")
    try:
        u.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        h = k.GlobalAlloc(0x0042, len(data))  # GMEM_MOVEABLE | GMEM_ZEROINIT
        p = k.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        u.SetClipboardData(13, h)  # CF_UNICODETEXT; el sistema queda dueño de h
    finally:
        u.CloseClipboard()


def press_paste():
    """Ctrl+V sintético. Si no hay campo de texto enfocado no pasa nada,
    pero el texto ya quedó en el clipboard (ese ES el fallback)."""
    from pynput.keyboard import Controller, Key
    kbd = Controller()
    with kbd.pressed(Key.ctrl):
        kbd.press("v")
        kbd.release("v")


def play_sound(alias):
    """Sonido de sistema de Windows por alias ("SystemAsterisk", etc.)."""
    if alias:
        import winsound
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)


# ---------------------------------------------------------------- audio
SAMPLE_RATE = 16000
_audio_buf = []
_recording = False
PILL = None  # instancia de pill.Pill en modo vivo; None en --test


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
            # suavizado exponencial: el waveform respira, no salta
            _level_smooth = 0.65 * _level_smooth + 0.35 * min(1.0, peak * 1.8)
            PILL.push_level(_level_smooth)


def warm_ollama():
    """Pre-carga el modelo de Ollama sin generar nada (messages=[] = preload).
    Se dispara al apretar Fn: mientras hablas, el modelo ya se está cargando."""
    def _ping():
        try:
            body = json.dumps({"model": CFG["ollama_model"], "messages": [],
                               "keep_alive": "10m"}).encode()
            req = urllib.request.Request(CFG["ollama_url"], data=body,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30)
        except Exception:
            pass  # si falla, clean() hará su propio fallback
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
    """-> (wav_bytes 16kHz, duration_s)"""
    global _recording
    _recording = False
    play_sound(CFG["sound_stop"])
    raw = b"".join(_audio_buf)
    duration = len(raw) / (_capture_rate * 2)  # int16 mono
    raw = _resample_16k(raw, _capture_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(raw)
    return buf.getvalue(), duration


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
    """-> (texto_crudo, ms)"""
    t0 = time.monotonic()
    fields = {
        "language": CFG["language"],
        "response_format": "json",
        "temperature": "0.0",
    }
    words = [e["written"] for e in dict_load()]
    if words:
        fields["prompt"] = ", ".join(words)  # sesga whisper hacia tu vocabulario
    resp = multipart_post(CFG["whisper_url"], fields, wav_bytes, timeout=120)
    text = " ".join(resp.get("text", "").split())  # whisper mete \n en el texto
    return text, int((time.monotonic() - t0) * 1000)


def _rewrote(raw, text):
    """True si el limpiador metió palabras que no estaban en el crudo.
    Su contrato es solo BORRAR muletillas: demasiada palabra nueva = tradujo,
    parafraseó o respondió como chatbot (el 3B unifica idiomas en dictados
    mixtos ES->EN; ver .tmp/HANDOFF.md)."""
    norm = lambda s: [w.strip(".,;:¿?¡!\"'()").lower() for w in s.split()]
    raw_words = set(norm(raw))
    out = [w for w in norm(text) if w]
    if not out:
        return True
    new = sum(1 for w in out if w not in raw_words)
    return new / len(out) > 0.2  # ponytail: umbral fijo; config si molesta


def clean(raw):
    """-> (texto_limpio, ms, fell_back). Nunca lanza: si Ollama falla, devuelve raw."""
    t0 = time.monotonic()
    try:
        body = json.dumps({
            "model": CFG["ollama_model"],
            # Input:/Output: replica el patrón de los ejemplos del system prompt:
            # el modelo completa la transformación en vez de "responder" al texto
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
                print("  ollama reescribió (palabras nuevas) -> uso texto crudo")
                return raw, int((time.monotonic() - t0) * 1000), True
            return text, int((time.monotonic() - t0) * 1000), False
    except Exception as e:
        print(f"  ollama fallo ({e!r}) -> uso texto crudo")
    return raw, int((time.monotonic() - t0) * 1000), True


# ---------------------------------------------------------------- historial
HISTORY_FILE = BASE / "history.jsonl"
_hist_lock = threading.Lock()


def history_append(text):
    with _hist_lock, open(HISTORY_FILE, "a") as f:
        f.write(json.dumps({"ts": time.time(), "text": text},
                           ensure_ascii=False) + "\n")


def _history_all():
    if not HISTORY_FILE.exists():
        return []
    with open(HISTORY_FILE) as f:
        return [json.loads(l) for l in f if l.strip()]


def history_read(limit=100):
    with _hist_lock:
        return list(reversed(_history_all()))[:limit]  # más nuevo primero


def history_delete(ts):
    with _hist_lock:
        items = [i for i in _history_all() if i["ts"] != ts]
        HISTORY_FILE.write_text(
            "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))


def history_clear():
    with _hist_lock:
        HISTORY_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------- diccionario
# Vocabulario propio: {"written": "n8n", "spoken": ["ene ocho ene", ...]}.
# La llave es lo que whisper ESCUCHA (se graba una vez desde los ajustes);
# el reemplazo es determinista, sin pasar por el LLM.
DICT_FILE = BASE / "dictionary.json"
_dict_rec = {"active": False, "buf": []}


def dict_load():
    if not DICT_FILE.exists():
        return []
    return json.loads(DICT_FILE.read_text())


def dict_save(entries):
    DICT_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")


def _norm_spoken(s):
    return " ".join(w.strip(".,;:¿?¡!\"'()").lower() for w in s.split())


def dict_record_word():
    """Graba ~2.5s del mic, transcribe y devuelve la llave hablada normalizada."""
    _dict_rec["buf"] = []
    _dict_rec["active"] = True
    play_sound(CFG["sound_start"])
    time.sleep(2.5)  # ponytail: ventana fija; basta para una palabra o sigla
    _dict_rec["active"] = False
    play_sound(CFG["sound_stop"])
    raw = _resample_16k(b"".join(_dict_rec["buf"]), _capture_rate)
    _dict_rec["buf"] = []
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(raw)
    spoken, _ = transcribe(buf.getvalue())
    return _norm_spoken(spoken)


def apply_dictionary(text):
    """Variante hablada -> forma escrita, case-insensitive. Tolera la
    puntuación que whisper mete entre palabras ("ene, ocho, ene")."""
    for e in dict_load():
        for v in sorted(e["spoken"], key=len, reverse=True):
            pat = r"\b" + r"[,.]*\s+".join(re.escape(w) for w in v.split()) + r"\b"
            text = re.sub(pat, lambda m: e["written"], text, flags=re.IGNORECASE)
    return text


# ---------------------------------------------------------------- audio stream
_sd = None      # módulo sounddevice (importado solo en modo vivo)
_stream = None


_capture_rate = SAMPLE_RATE  # frecuencia real del stream (16k, o nativa del mic)


def open_stream():
    """(Re)abre el stream de mic según CFG['mic'] (None = default del sistema).
    Si el mic no acepta 16kHz (típico USB 48k como el Shure MV7+), abre a su
    frecuencia nativa y stop_recording() remuestrea a 16k para Whisper."""
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
    # ponytail: stream siempre abierto (punto naranja fijo) : evita cortar la
    # primera palabra; cambiar a open/close por dictada si molesta.
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
        log(f"mic a {native}Hz (no acepta 16k), remuestreo activado")
    _stream.start()


def _resample_16k(raw, rate):
    """Remuestreo lineal int16 mono -> 16kHz. Suficiente para voz + Whisper."""
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


# ---------------------------------------------------------------- servidor UI
def save_config():
    (BASE / "config.json").write_text(
        json.dumps(CFG, indent=2, ensure_ascii=False) + "\n")


def start_ui_server():
    """Página de ajustes en localhost:8091. El bind ES el candado de instancia única."""
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
                f = BASE / "fonts" / Path(self.path).name  # .name evita traversal
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
                        return self._send(409, "Termina el dictado primero".encode(),
                                          "text/plain; charset=utf-8")
                    prev = CFG.get("mic")
                    CFG["mic"] = body["mic"] or None
                    try:
                        open_stream()
                    except Exception as e:
                        CFG["mic"] = prev
                        open_stream()
                        return self._send(400, f"Ese mic falló: {e}".encode(),
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
            elif self.path == "/api/history/delete":
                history_delete(body.get("ts"))
                self._send(200, {"ok": True})
            elif self.path == "/api/history/clear":
                history_clear()
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})

    # instancia única: si alguien RESPONDE en 8091 es un Orac Voice vivo.
    # (allow_reuse_address permite relanzar al tiro tras cerrar, sin TIME_WAIT)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{UI_PORT}/api/state", timeout=1)
        # segundo doble-click con la app ya viva: mostrar los ajustes, no morir mudo
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{UI_PORT}")
        sys.exit(0)
    except (urllib.error.URLError, OSError):
        pass

    class Srv(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = Srv(("127.0.0.1", UI_PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


# ---------------------------------------------------------------- whisper-server
def ensure_whisper():
    base = CFG["whisper_url"].rsplit("/", 1)[0]
    try:
        urllib.request.urlopen(base, timeout=2)
        return  # ya corre (arrancado a mano) : idempotente
    except Exception:
        pass
    if not CFG["whisper_autostart"]:
        sys.exit(f"whisper-server no responde en {base} y autostart está apagado")
    print("Levantando whisper-server...")
    bin_ = Path(CFG["whisper_server_bin"])
    if not bin_.is_absolute():
        bin_ = BASE / bin_
    proc = subprocess.Popen(
        [str(bin_), "-m", str(BASE / CFG["whisper_model"]),
         "--host", "127.0.0.1", "--port", base.rsplit(":", 1)[1]],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    atexit.register(proc.terminate)
    # ponytail: sin supervisión de restart : si el server muere después, el error
    # se loguea en cada dictada y reinicias flow.py.
    for _ in range(60):
        time.sleep(1)
        try:
            urllib.request.urlopen(base, timeout=2)
            print("whisper-server listo.")
            return
        except Exception:
            continue
    sys.exit("whisper-server no levantó en 60s")


# ---------------------------------------------------------------- máquina de estados
IDLE, RECORDING, MAYBE_HANDSFREE, HANDSFREE, PROCESSING = range(5)
_state = IDLE
_lock = threading.Lock()
_t_down = 0.0
_hf_timer = None


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} | {msg}", flush=True)


def finish(wav_bytes, duration):
    """Worker por dictada. Corre en thread propio; el tap nunca espera."""
    global _state
    t0 = time.monotonic()
    try:
        if duration < CFG["min_record_s"]:
            log(f"descartada (muy corta: {duration:.2f}s)")
            play_sound(CFG["sound_error"])
            return
        raw, ms_w = transcribe(wav_bytes)
        if not raw:
            log("descartada (whisper no oyó nada)")
            play_sound(CFG["sound_error"])
            return
        text, ms_o, fell_back = clean(raw)
        text = apply_dictionary(text)
        set_clipboard(text)
        time.sleep(0.05)
        press_paste()
        history_append(text)
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
            _state = IDLE  # nunca quedar pegado en PROCESSING


def _spawn_finish():
    """Llamar con _lock tomado: cierra la grabación y lanza el worker."""
    global _state
    wav, dur = stop_recording()
    _state = PROCESSING
    if PILL:
        PILL.show_processing()
    threading.Thread(target=finish, args=(wav, dur), daemon=True).start()


def _hf_window_expired():
    """Timer: el tap corto no fue doble-tap -> descartar (tap accidental)."""
    global _state
    with _lock:
        if _state == MAYBE_HANDSFREE:
            stop_recording()
            log("descartada (tap accidental)")
            if PILL:
                PILL.hide()
            _state = IDLE


def cancel_dictation():
    """X de la pastilla o tecla Escape: descartar lo grabado, sin acción."""
    global _state
    if _capture["active"]:
        _capture["active"] = False  # Escape también cancela la captura de tecla
        return
    with _lock:
        if _state in (RECORDING, MAYBE_HANDSFREE, HANDSFREE):
            if _hf_timer:
                _hf_timer.cancel()
            stop_recording()
            log("cancelada (X/Esc)")
            if PILL:
                PILL.hide()
            _state = IDLE


def confirm_dictation():
    """Click en el ✓ de la pastilla: terminar y procesar ya."""
    with _lock:
        if _state in (RECORDING, MAYBE_HANDSFREE, HANDSFREE):
            if _hf_timer:
                _hf_timer.cancel()
            log("confirmada (✓), procesando...")
            _spawn_finish()


def on_fn_down():
    global _state, _t_down, _hf_timer
    with _lock:
        if _state == IDLE:
            start_recording()
            _t_down = time.monotonic()
            _state = RECORDING
            log("grabando (hold)...")
        elif _state == MAYBE_HANDSFREE:
            _hf_timer.cancel()
            _state = HANDSFREE  # la grabación nunca paró: sin gap de audio
            log("manos libres ON (Fn para parar)")
        elif _state == HANDSFREE:
            log("manos libres OFF, procesando...")
            _spawn_finish()
        elif _state == PROCESSING:
            log("busy : todavía transcribiendo la anterior")


def on_fn_up():
    global _state, _hf_timer
    with _lock:
        if _state == RECORDING:
            held = time.monotonic() - _t_down
            if held >= CFG["double_tap_ms"] / 1000:
                log("procesando...")
                _spawn_finish()
            else:
                # tap corto: puede ser inicio de doble-tap; la grabación sigue
                _state = MAYBE_HANDSFREE
                _hf_timer = threading.Timer(CFG["double_tap_ms"] / 1000,
                                            _hf_window_expired)
                _hf_timer.start()
        # HANDSFREE: ignorar (cola del segundo tap). PROCESSING/IDLE: nada.


# ---------------------------------------------------------------- main
def run_test(wav_path):
    """Pipeline headless sobre un WAV: el self-check E2E (sin hotkey ni mic)."""
    ensure_whisper()
    wav = Path(wav_path).read_bytes()
    with wave.open(wav_path, "rb") as w:
        duration = w.getnframes() / w.getframerate()
    t0 = time.monotonic()
    raw, ms_w = transcribe(wav)
    assert raw, "whisper devolvió texto vacío"
    text, ms_o, fell_back = clean(raw)
    text = apply_dictionary(text)
    assert text, "pipeline devolvió texto vacío"
    set_clipboard(text)
    tag = "FALLBACK raw" if fell_back else "ok"
    log(f"rec {duration:.1f}s | whisper {ms_w}ms | ollama {ms_o}ms ({tag}) | "
        f"total {time.monotonic() - t0:.2f}s | {len(text)} chars")
    print(f"CRUDO : {raw}\nLIMPIO: {text}\n(el texto limpio quedó en el clipboard)")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--test":
        run_test(sys.argv[2])
        return
    global _sd, PILL
    import sounddevice
    _sd = sounddevice
    start_ui_server()  # también actúa de candado de instancia única
    ensure_whisper()
    open_stream()

    import pill as pillmod
    PILL = pillmod.Pill()  # solo visual: Esc cancela, la tecla confirma
    setup_hotkey_listener(on_fn_down, on_fn_up, on_escape=cancel_dictation)
    print(f"Orac Voice listo. Mantén {CFG['hotkey']['label']} para dictar; "
          "doble-tap = manos libres. Ajustes: http://127.0.0.1:8091")
    PILL.run()  # tkinter mainloop en el thread principal


if __name__ == "__main__":
    main()
