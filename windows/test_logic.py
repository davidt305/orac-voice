"""Orac Voice Windows logic self-test. Runs on any OS, without audio or
pynput: stubs winsound, starts the API on port 8099 and exercises the
state machine, the key capture and the endpoints.

SAFE FOR MACHINES IN USE: backs up config.json, history.jsonl and
dictionary.json to *.bak before starting and restores them at the end, no
matter what. If the process dies midway, the .bak files stay on disk for
manual recovery.

Usage: python test_logic.py  (must end with "ALL OK")
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

flow.UI_PORT = 8099                 # don't collide with a live app on 8091
flow.play_sound = lambda *a: None   # no audio
flow.warm_ollama = lambda: None     # no network
flow.set_clipboard = lambda t: None

# --- safeguard: the test touches the real files, so .bak first
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
        assert time.monotonic() - t0 < timeout, "did not return to IDLE"
        time.sleep(0.05)


def api(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:8099{path}", data=data)
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


try:
    orig_key = flow.CFG["hotkey"]["key"]     # respect the user's binding
    other = "shift_r" if orig_key != "shift_r" else "alt_l"

    # --- 1. hold: down + release after >= double_tap_ms -> processes, back to IDLE
    flow.on_fn_down()
    assert flow._state == flow.RECORDING
    flow._t_down = time.monotonic() - 0.5    # simulates 0.5s of key held down
    flow.on_fn_up()
    wait_idle()

    # --- 2. hands-free double-tap: short down/up, down (ON), down (processes)
    flow.on_fn_down()
    flow.on_fn_up()
    assert flow._state == flow.MAYBE_HANDSFREE
    flow.on_fn_down()
    assert flow._state == flow.HANDSFREE
    flow.on_fn_down()
    wait_idle()

    # --- 3. Escape cancels an in-progress dictation
    flow.on_fn_down()
    assert flow._state == flow.RECORDING
    flow.cancel_dictation()
    assert flow._state == flow.IDLE

    # --- 4. _on_key: auto-repeat guard (Windows repeats the keydown)
    downs = []
    for _ in range(5):
        flow._on_key(orig_key, True, lambda: downs.append(1), lambda: None)
    flow._on_key(orig_key, False, lambda: None, lambda: downs.append("up"))
    assert downs == [1, "up"], downs
    wait_idle()

    # --- 5. capture: no dictation while capturing, and it returns the key
    flow._capture["active"] = True
    flow._on_key(other, True, lambda: downs.append("no!"), lambda: None)
    assert flow._capture["result"] == {"key": other,
                                       "label": flow.CAPTURE_KEYS[other]}
    assert flow._state == flow.IDLE and "no!" not in downs

    # --- 6. local API
    flow.start_ui_server()
    st = api("/api/state")
    assert st["config"]["hotkey"]["key"] == orig_key
    assert "mics" in st and "history" in st and "dictionary" in st

    api("/api/config", {"hotkey": {"key": other}})
    assert flow._watch[0] == other
    api("/api/config", {"hotkey": {"key": orig_key}})   # back to the user's key
    assert flow._watch[0] == orig_key

    api("/api/capture/start", {})
    assert flow._capture["active"]
    flow._on_key(other, True, lambda: None, lambda: None)
    assert api("/api/capture")["result"]["key"] == other

    flow.history_append("QC test")
    assert api("/api/state")["history"][0]["text"] == "QC test"
    api("/api/history/clear", {})
    assert api("/api/state")["history"] == []

    api("/api/dict/delete", {"written": "word-that-does-not-exist"})
    assert isinstance(api("/api/state")["dictionary"], list)

    print("ALL OK: state machine, hotkey, capture and API")
finally:
    _restore()
