"""Self-test de lógica de Orac Voice Windows. Corre en cualquier OS, sin audio
ni pynput: stubbea winsound, levanta la API en el puerto 8099 y ejercita la
máquina de estados, la captura de tecla y los endpoints.

SEGURO PARA MÁQUINAS EN USO: respalda config.json, history.jsonl y
dictionary.json a *.bak antes de partir y los restaura al final, pase lo que
pase. Si el proceso muere a la mitad, los .bak quedan en disco para
recuperarlos a mano.

Uso: python test_logic.py  (debe terminar con "TODO OK")
"""
import json
import sys
import time
import types
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.modules.setdefault("winsound", types.SimpleNamespace(
    PlaySound=lambda *a, **k: None, SND_ALIAS=0, SND_ASYNC=0))

import flow

flow.UI_PORT = 8099                 # no chocar con una app viva en 8091
flow.play_sound = lambda *a: None   # sin audio
flow.warm_ollama = lambda: None     # sin red
flow.set_clipboard = lambda t: None

# --- resguardo: el test toca los archivos reales, así que primero .bak
_DATA = [HERE / "config.json", flow.HISTORY_FILE, flow.DICT_FILE]
for p in _DATA:
    if p.exists():
        p.with_suffix(p.suffix + ".bak").write_bytes(p.read_bytes())


def _restore():
    for p in _DATA:
        bak = p.with_suffix(p.suffix + ".bak")
        if bak.exists():
            p.write_bytes(bak.read_bytes())
            bak.unlink()
        else:
            p.unlink(missing_ok=True)


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


try:
    orig_key = flow.CFG["hotkey"]["key"]     # respetar el binding del usuario
    other = "shift_r" if orig_key != "shift_r" else "alt_l"

    # --- 1. hold: down + soltar tras >= double_tap_ms -> procesa y a IDLE
    flow.on_fn_down()
    assert flow._state == flow.RECORDING
    flow._t_down = time.monotonic() - 0.5    # simula 0.5s de tecla abajo
    flow.on_fn_up()
    wait_idle()

    # --- 2. doble-tap manos libres: down/up corto, down (ON), down (procesa)
    flow.on_fn_down()
    flow.on_fn_up()
    assert flow._state == flow.MAYBE_HANDSFREE
    flow.on_fn_down()
    assert flow._state == flow.HANDSFREE
    flow.on_fn_down()
    wait_idle()

    # --- 3. Escape cancela un dictado en curso
    flow.on_fn_down()
    assert flow._state == flow.RECORDING
    flow.cancel_dictation()
    assert flow._state == flow.IDLE

    # --- 4. _on_key: guard de auto-repeat (Windows repite el keydown)
    downs = []
    for _ in range(5):
        flow._on_key(orig_key, True, lambda: downs.append(1), lambda: None)
    flow._on_key(orig_key, False, lambda: None, lambda: downs.append("up"))
    assert downs == [1, "up"], downs
    wait_idle()

    # --- 5. captura: mientras captura no se dicta, y devuelve la tecla
    flow._capture["active"] = True
    flow._on_key(other, True, lambda: downs.append("no!"), lambda: None)
    assert flow._capture["result"] == {"key": other,
                                       "label": flow.CAPTURE_KEYS[other]}
    assert flow._state == flow.IDLE and "no!" not in downs

    # --- 6. API local
    flow.start_ui_server()
    st = api("/api/state")
    assert st["config"]["hotkey"]["key"] == orig_key
    assert "mics" in st and "history" in st and "dictionary" in st

    api("/api/config", {"hotkey": {"key": other}})
    assert flow._watch[0] == other
    api("/api/config", {"hotkey": {"key": orig_key}})   # de vuelta al del usuario
    assert flow._watch[0] == orig_key

    api("/api/capture/start", {})
    assert flow._capture["active"]
    flow._on_key(other, True, lambda: None, lambda: None)
    assert api("/api/capture")["result"]["key"] == other

    flow.history_append("prueba QC")
    assert api("/api/state")["history"][0]["text"] == "prueba QC"
    api("/api/history/clear", {})
    assert api("/api/state")["history"] == []

    api("/api/dict/delete", {"written": "palabra-que-no-existe"})
    assert isinstance(api("/api/state")["dictionary"], list)

    print("TODO OK: máquina de estados, hotkey, captura y API")
finally:
    _restore()
