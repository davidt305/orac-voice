#!/usr/bin/env python3
"""flow-local: clon local de Wispr Flow.

Mantén Fn para dictar; suelta y el texto limpio se pega en el cursor.
Doble-tap Fn = manos libres (un Fn más lo detiene).
Pipeline: mic -> whisper-server (local) -> Ollama (limpieza) -> clipboard + Cmd+V.

Uso:
  .venv/bin/python flow.py              # daemon en vivo (necesita permisos, ver README)
  .venv/bin/python flow.py --test x.wav # pipeline headless sobre un WAV, sin hotkey/mic
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

IS_MAC = sys.platform == "darwin"
if IS_MAC:
    import Quartz
    from AppKit import NSPasteboard, NSPasteboardTypeString

# teclas modificadoras bindeables: keycode -> (label, máscara en flagsChanged)
CAPTURE_KEYS = {
    63: ("Fn (Globe)", 0x800000),
    54: ("Right ⌘", 0x100000), 55: ("Left ⌘", 0x100000),
    61: ("Right ⌥", 0x80000),  58: ("Left ⌥", 0x80000),
    62: ("Right ⌃", 0x40000),  59: ("Left ⌃", 0x40000),
    60: ("Right ⇧", 0x20000),  56: ("Left ⇧", 0x20000),
}
# migración: config viejo guardaba "hotkey": "fn" como string
if isinstance(CFG.get("hotkey"), str):
    _old = {"fn": 63, "right_cmd": 54, "right_option": 61, "right_ctrl": 62}
    code = _old.get(CFG["hotkey"], 63)
    CFG["hotkey"] = {"keycode": code, "mask": CAPTURE_KEYS[code][1],
                     "label": CAPTURE_KEYS[code][0]}
_watch = [CFG["hotkey"]["keycode"], CFG["hotkey"]["mask"]]

# modo captura: la página pide "detecta la próxima tecla que apriete el usuario"
_capture = {"active": False, "result": None}
_watched_down = False


def _on_flags_changed(keycode, flags, on_down, on_up):
    """Lógica de flagsChanged, separada del callback para test y port Windows."""
    global _watched_down
    if _capture["active"]:
        info = CAPTURE_KEYS.get(keycode)
        if info and (flags & info[1]):  # tecla modificadora presionada
            _capture["active"] = False
            _capture["result"] = {"keycode": keycode, "mask": info[1],
                                  "label": info[0]}
        return  # mientras capturas, no se dicta
    if keycode == _watch[0]:
        down = bool(flags & _watch[1])
        if down and not _watched_down:
            _watched_down = True
            on_down()
        elif not down and _watched_down:
            _watched_down = False
            on_up()

# ---------------------------------------------------------------- plataforma
# Las funciones de esta sección son la ÚNICA diferencia con la versión
# Windows, que vive completa y autocontenida en windows/flow.py.

def setup_hotkey_tap(on_down, on_up, on_escape=None):
    """Escucha la tecla de dictado (flagsChanged) + Escape (keyDown) vía
    CGEventTap en el runloop actual. No bloquea: el caller corre el event loop."""
    if not IS_MAC:
        raise NotImplementedError("En Windows usa windows/flow.py")

    def callback(proxy, type_, event, refcon):
        if type_ in (Quartz.kCGEventTapDisabledByTimeout,
                     Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(tap, True)  # macOS desactiva taps lentos; revivir
            return event
        keycode = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode)
        if type_ == Quartz.kCGEventFlagsChanged:
            _on_flags_changed(keycode, Quartz.CGEventGetFlags(event),
                              on_down, on_up)
        elif type_ == Quartz.kCGEventKeyDown and keycode == 53 and on_escape:
            on_escape()  # 53 = Escape
        return event

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown),
        callback, None)
    if tap is None:
        print("ERROR: macOS no permitió escuchar el teclado.\n"
              "  System Settings → Privacy & Security → Input Monitoring: activa tu Terminal\n"
              "  System Settings → Privacy & Security → Accessibility: activa tu Terminal\n"
              "  Luego cierra y reabre la Terminal y vuelve a correr flow.py")
        sys.exit(1)
    src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src,
                              Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    return tap


def set_clipboard(text):
    if not IS_MAC:
        raise NotImplementedError("En Windows usa windows/flow.py")
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)


def press_paste():
    """Cmd+V sintético. Si no hay campo de texto enfocado no pasa nada,
    pero el texto ya quedó en el clipboard (ese ES el fallback)."""
    if not IS_MAC:
        raise NotImplementedError("En Windows usa windows/flow.py")
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, 9, down)  # 9 = kVK_ANSI_V
        Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def play_sound(path):
    if path:
        subprocess.Popen(["afplay", path])  # fire-and-forget


def _quit_app():
    """Quit limpio desde la página de ajustes (botón Quit)."""
    if _whisper_proc:
        _whisper_proc.terminate()
    from AppKit import NSApplication
    from PyObjCTools import AppHelper
    AppHelper.callAfter(NSApplication.sharedApplication().terminate_, None)


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
    """-> (raw_bytes, rate, duration_s). Barato a propósito: corre en el
    callback del hotkey; el resample/encode pesado va en el worker (finish)."""
    global _recording
    _recording = False
    play_sound(CFG["sound_stop"])
    raw = b"".join(_audio_buf)
    return raw, _capture_rate, len(raw) / (_capture_rate * 2)  # int16 mono


def _encode_wav16k(raw, rate):
    """int16 mono a cualquier rate -> bytes de WAV 16kHz para whisper."""
    raw = _resample_16k(raw, rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(raw)
    return buf.getvalue()


def _is_silence(raw):
    """True si el audio no tiene voz (mic muteado/desconectado). Sin este gate
    whisper alucina frases tipo "Thank you." sobre el silencio."""
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


def _norm_words(s):
    """Palabras comparables: sin puntuación en los bordes, en minúscula.
    Compartida por el guard del limpiador y las llaves del diccionario."""
    return [w.strip(".,;:¿?¡!\"'()").lower() for w in s.split()]


def _rewrote(raw, text):
    """True si el limpiador metió palabras que no estaban en el crudo.
    Su contrato es solo BORRAR muletillas: demasiada palabra nueva significa
    que tradujo, parafraseó o respondió como chatbot (llama3.2:3b tiende a
    unificar dictados bilingües al idioma de la primera frase)."""
    raw_words = set(_norm_words(raw))
    out = [w for w in _norm_words(text) if w]
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
    with _hist_lock:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "text": text},
                               ensure_ascii=False) + "\n")
        items = _history_all()
        if len(items) > 1000:  # ponytail: cap fijo; el poll de 5s no crece eterno
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
        return list(reversed(_history_all()))[:limit]  # más nuevo primero


def history_delete(ts):
    with _hist_lock:
        items = [i for i in _history_all() if i["ts"] != ts]
        HISTORY_FILE.write_text(
            "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items),
            encoding="utf-8")


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
    return json.loads(DICT_FILE.read_text(encoding="utf-8"))


def dict_save(entries):
    DICT_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")


def _norm_spoken(s):
    return " ".join(_norm_words(s))


def dict_record_word():
    """Graba ~2.5s del mic, transcribe y devuelve la llave hablada normalizada."""
    _dict_rec["buf"] = []
    _dict_rec["active"] = True
    play_sound(CFG["sound_start"])
    time.sleep(2.5)  # ponytail: ventana fija; basta para una palabra o sigla
    _dict_rec["active"] = False
    play_sound(CFG["sound_stop"])
    raw = b"".join(_dict_rec["buf"])
    _dict_rec["buf"] = []
    if _is_silence(raw):
        return ""
    spoken, _ = transcribe(_encode_wav16k(raw, _capture_rate))
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
        json.dumps(CFG, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
                if _state != IDLE:
                    # capturar mientras se dicta se tragaría el release de la
                    # tecla sostenida y el mic quedaría grabando para siempre
                    return self._send(409, "Termina el dictado primero".encode(),
                                      "text/plain; charset=utf-8")
                _capture["active"] = True
                _capture["result"] = None
                return self._send(200, {"ok": True})
            if self.path == "/api/capture/cancel":
                _capture["active"] = False
                return self._send(200, {"ok": True})
            if self.path == "/api/config":
                hk = body.get("hotkey")
                if isinstance(hk, dict) and hk.get("keycode") in CAPTURE_KEYS:
                    mask = CAPTURE_KEYS[hk["keycode"]][1]
                    CFG["hotkey"] = {"keycode": hk["keycode"], "mask": mask,
                                     "label": CAPTURE_KEYS[hk["keycode"]][0]}
                    _watch[:] = [hk["keycode"], mask]
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

    try:
        srv = Srv(("127.0.0.1", UI_PORT), Handler)
    except OSError:
        sys.exit(f"El puerto {UI_PORT} está ocupado por otro programa; "
                 "ciérralo o cambia UI_PORT en flow.py")
    threading.Thread(target=srv.serve_forever, daemon=True).start()


# ---------------------------------------------------------------- whisper-server
_whisper_proc = None  # hijo nuestro (None si el server ya corría de antes)


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
    global _whisper_proc
    _whisper_proc = subprocess.Popen(
        [str(bin_), "-m", str(BASE / CFG["whisper_model"]),
         "--host", "127.0.0.1", "--port", base.rsplit(":", 1)[1]],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    atexit.register(_whisper_proc.terminate)
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


def finish(raw_audio, rate, duration):
    """Worker por dictada. Corre en thread propio; el tap nunca espera
    (el resample/encode pesado de audios largos vive aquí, no en el tap)."""
    global _state
    t0 = time.monotonic()
    try:
        if duration < CFG["min_record_s"]:
            log(f"descartada (muy corta: {duration:.2f}s)")
            play_sound(CFG["sound_error"])
            return
        if _is_silence(raw_audio):
            log("descartada (silencio: ¿mic muteado?)")
            play_sound(CFG["sound_error"])
            return
        raw, ms_w = transcribe(_encode_wav16k(raw_audio, rate))
        if not raw:
            log("descartada (whisper no oyó nada)")
            play_sound(CFG["sound_error"])
            return
        text, ms_o, fell_back = clean(raw)
        text = apply_dictionary(text)
        history_append(text)  # primero: si el clipboard falla, el texto sobrevive
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
            _state = IDLE  # nunca quedar pegado en PROCESSING


def _spawn_finish():
    """Llamar con _lock tomado: cierra la grabación y lanza el worker."""
    global _state
    raw, rate, dur = stop_recording()
    _state = PROCESSING
    if PILL:
        PILL.show_processing()
    threading.Thread(target=finish, args=(raw, rate, dur), daemon=True).start()


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
    global _sd
    import sounddevice
    _sd = sounddevice
    start_ui_server()  # también actúa de candado de instancia única
    ensure_whisper()
    open_stream()

    global PILL
    import pill as pillmod
    from PyObjCTools import AppHelper
    pillmod.make_app()
    PILL = pillmod.Pill(on_cancel=cancel_dictation, on_confirm=confirm_dictation)
    pillmod.make_menubar()
    setup_hotkey_tap(on_fn_down, on_fn_up, on_escape=cancel_dictation)
    print(f"Orac Voice listo. Mantén {CFG['hotkey']['label']} para dictar; "
          f"doble-tap = manos libres. Ajustes: http://127.0.0.1:{UI_PORT}")
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
