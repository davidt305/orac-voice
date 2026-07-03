"""Orac Voice visual pill for Windows (tkinter, stdlib).

Same look as the Mac pill (Higgsfield palette): dark capsule with a lime
outline, 11 white waveform bars easing toward the mic level while recording,
8 pulsing dots while processing. Indicator only, no buttons: Escape cancels
and the dictation key confirms. The window is click-through and never steals
focus (WS_EX_NOACTIVATE + WS_EX_TRANSPARENT), so the Ctrl+V always reaches
the user's text field. Same interface as the Mac pill.py: show_recording /
show_processing / hide / push_level, plus run() which runs the mainloop on
the main thread.
"""
import ctypes
import math
import time
import tkinter as tk
from collections import deque

LIME = "#ccff00"
BG = "#141414"
CHROMA = "#010101"  # color reserved for transparency (never drawn)
W, H = 170, 36
N_BARS, N_DOTS = 11, 8
SPAN_PAD = 18       # center zone margin at each end (no buttons on Windows)

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080


def _capsule(c, x0, y0, x1, y1, color):
    """Rounded-full shape on a Canvas: two circles + a rectangle."""
    d = y1 - y0
    c.create_oval(x0, y0, x0 + d, y1, fill=color, outline=color)
    c.create_oval(x1 - d, y0, x1, y1, fill=color, outline=color)
    c.create_rectangle(x0 + d // 2, y0, x1 - d // 2, y1,
                       fill=color, outline=color)


class Pill:
    def __init__(self):
        self._want = "hidden"   # written from other threads; reading a str is atomic
        self._shown = None
        self.levels = deque([0.05] * N_BARS, maxlen=N_BARS)
        self.display = [0.05] * N_BARS  # animated heights (easing toward levels)
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.0)  # hides without unmap (keeps styles)
        self.root.attributes("-transparentcolor", CHROMA)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw - W) // 2}+{sh - H - 60}")
        c = tk.Canvas(self.root, width=W, height=H, bg=CHROMA,
                      highlightthickness=0)
        c.pack()
        # lime outline: lime capsule with a 2px-inset dark capsule on top
        _capsule(c, 0, 0, W, H, LIME)
        _capsule(c, 2, 2, W - 2, H - 2, BG)
        cy = H // 2
        x0, x1 = SPAN_PAD, W - SPAN_PAD
        span = x1 - x0
        # waveform bars (recording): brighter toward the center, like the Mac
        self.bars = []
        step = span / N_BARS
        bw = 3
        for i in range(N_BARS):
            edge = 1.0 - 0.5 * abs(i - (N_BARS - 1) / 2) / ((N_BARS - 1) / 2)
            shade = int(255 * (0.5 + 0.5 * edge))
            bx = x0 + step * i + (step - bw) / 2
            self.bars.append(c.create_rectangle(
                bx, cy - 2, bx + bw, cy + 2, outline="",
                fill=f"#{shade:02x}{shade:02x}{shade:02x}", state="hidden"))
        # pulsing dots (processing)
        self.dots = []
        step = span / N_DOTS
        for i in range(N_DOTS):
            dx = x0 + step * i + step / 2
            self.dots.append(c.create_oval(dx - 2, cy - 2, dx + 2, cy + 2,
                                           outline="", state="hidden"))
        self.canvas = c
        self.root.update_idletasks()
        self._make_ghost()

    def _make_ghost(self):
        """Steals no focus, lets clicks pass through, hidden from Alt+Tab."""
        u = ctypes.windll.user32
        hwnd = u.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE
                         | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)

    # ---- API called from the daemon threads (only sets the state)
    def show_recording(self):
        self._want = "recording"

    def show_processing(self):
        self._want = "processing"

    def hide(self):
        self._want = "hidden"

    def push_level(self, level):
        # appendleft: new samples enter on the left and flow to the right
        self.levels.appendleft(max(0.03, min(1.0, level)))

    def quit(self):
        self._want = "quit"  # _tick applies it on the tkinter thread

    # ---- applied on the tkinter thread via polling (~30 fps)
    def _tick(self):
        if self._want == "quit":
            self.root.destroy()
            return
        if self._want != self._shown:
            self._shown = self._want
            rec = self._shown == "recording"
            for b in self.bars:
                self.canvas.itemconfig(b, state="normal" if rec else "hidden")
            proc = self._shown == "processing"
            for d in self.dots:
                self.canvas.itemconfig(d, state="normal" if proc else "hidden")
            if self._shown == "hidden":
                self.root.attributes("-alpha", 0.0)
            else:
                self.root.attributes("-alpha", 1.0)
                self.root.attributes("-topmost", True)
        cy = H // 2
        if self._shown == "recording":
            targets = list(self.levels)  # newest first = leftmost bar
            for i, b in enumerate(self.bars):
                tgt = targets[i] if i < len(targets) else 0.05
                self.display[i] += (tgt - self.display[i]) * 0.35  # ease
                h = 3 + self.display[i] * 14
                x0b, _, x1b, _ = self.canvas.coords(b)
                self.canvas.coords(b, x0b, cy - h / 2, x1b, cy + h / 2)
        elif self._shown == "processing":
            t = time.monotonic()
            for i, d in enumerate(self.dots):
                a = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(t * 6 - i * 0.7))
                shade = int(0x14 + a * (255 - 0x14))
                self.canvas.itemconfig(
                    d, fill=f"#{shade:02x}{shade:02x}{shade:02x}")
        self.root.after(33, self._tick)

    def run(self):
        self.root.after(33, self._tick)
        self.root.mainloop()


if __name__ == "__main__":
    # standalone demo: recording 4s (fake waveform) -> processing 2.5s -> quit
    import threading

    pill = Pill()

    def fake():
        t0 = time.monotonic()
        while time.monotonic() - t0 < 4:
            t = time.monotonic()
            pill.push_level(0.3 + 0.7 * abs(math.sin(t * 8))
                            * (0.4 + 0.6 * math.sin(t * 1.7) ** 2))
            time.sleep(0.05)
        pill.show_processing()
        time.sleep(2.5)
        pill.quit()

    pill.show_recording()
    threading.Thread(target=fake, daemon=True).start()
    pill.run()
