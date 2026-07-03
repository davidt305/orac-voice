"""Self-test de lógica de Orac Voice Windows. Corre en cualquier OS, sin audio
ni pynput: stubbea winsound, levanta la API en el puerto 8099 y ejercita la
máquina de estados, la captura de tecla y los endpoints.

Uso: python test_logic.py  (debe terminar con "TODO OK")
"""
import json
import sys
import time
import types
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.modules.setdefault("winsound", types.SimpleNamespace(
    PlaySound=lambda *a, **k: None, SND_ALIAS=0, SND_ASYNC=0))

import flow

flow.UI_PORT = 8099                 # no chocar con una app viva en 8091
flow.play_sound = lambda *a: None   # sin audio
flow.warm_ollama = lambda: None     # sin red
flow.set_clipboard = lambda t: None


def wait_idle(timeout=3.0):
    t0 = time.monotonic()
    while flow._state != flow.IDLE:
        assert time.monotonic() - t0 < timeout, "no volvió a IDLE"
        time.sleep(0.05)


def api(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:8099{path}", data=data)
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


# --- 1. hold: down + soltar tras >= double_tap_ms -> procesa y vuelve a IDLE
flow.on_fn_down()
assert flow._state == flow.RECORDING
flow._t_down = time.monotonic() - 0.5   # simula que la tecla estuvo 0.5s abajo
flow.on_fn_up()
wait_idle()

# --- 2. doble-tap manos libres: down/up corto, down (ON), down (OFF y procesa)
flow.on_fn_down()
flow.on_fn_up()                          # tap corto -> MAYBE_HANDSFREE
assert flow._state == flow.MAYBE_HANDSFREE
flow.on_fn_down()                        # segundo tap -> HANDSFREE
assert flow._state == flow.HANDSFREE
flow.on_fn_down()                        # un tap más -> procesar
wait_idle()

# --- 3. Escape cancela un dictado en curso
flow.on_fn_down()
assert flow._state == flow.RECORDING
flow.cancel_dictation()
assert flow._state == flow.IDLE

# --- 4. _on_key: guard de auto-repeat (Windows repite keydown al mantener)
downs = []
for _ in range(5):
    flow._on_key("ctrl_r", True, lambda: downs.append(1), lambda: None)
flow._on_key("ctrl_r", False, lambda: None, lambda: downs.append("up"))
assert downs == [1, "up"], downs
wait_idle()

# --- 5. captura de tecla: mientras captura no se dicta, y devuelve la tecla
flow._capture["active"] = True
flow._on_key("shift_r", True, lambda: downs.append("no!"), lambda: None)
assert flow._capture["result"] == {"key": "shift_r", "label": "Right Shift"}
assert flow._state == flow.IDLE and "no!" not in downs

# --- 6. API local
flow.start_ui_server()
st = api("/api/state")
assert st["config"]["hotkey"]["key"] == "ctrl_r"
assert "mics" in st and "history" in st and "dictionary" in st

api("/api/config", {"hotkey": {"key": "shift_r"}})
assert flow._watch[0] == "shift_r"
api("/api/config", {"hotkey": {"key": "ctrl_r"}})    # restaurar default
assert flow._watch[0] == "ctrl_r"

api("/api/capture/start", {})
assert flow._capture["active"]
flow._on_key("cmd", True, lambda: None, lambda: None)
assert api("/api/capture")["result"]["label"] == "Left Win"

flow.history_append("prueba uno")
assert api("/api/state")["history"][0]["text"] == "prueba uno"
api("/api/history/clear", {})
assert api("/api/state")["history"] == []

api("/api/dict/delete", {"written": "nada"})
assert api("/api/state")["dictionary"] == []

# --- limpieza de artefactos del test
flow.HISTORY_FILE.unlink(missing_ok=True)
flow.DICT_FILE.unlink(missing_ok=True)

print("TODO OK: máquina de estados, hotkey, captura y API")
