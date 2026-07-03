"""Orac Voice visual pill for Windows (tkinter, stdlib).

Indicator only, no buttons: Escape cancels and the dictation key confirms.
The window is click-through and never steals focus (WS_EX_NOACTIVATE +
WS_EX_TRANSPARENT), so the Ctrl+V always reaches the user's text field.
Same interface as the Mac pill.py: show_recording / show_processing / hide /
push_level, plus run() which runs the mainloop on the main thread.
"""
import ctypes
import tkinter as tk

LIME = "#ccff00"
BG = "#0a0a0a"
CHROMA = "#010101"  # color reserved for transparency (never drawn)
W, H = 170, 36

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080


class Pill:
    def __init__(self):
        self._want = "hidden"   # written from other threads; reading a str is atomic
        self._shown = None
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
        r = H // 2  # capsule: two circles + a rectangle
        c.create_oval(0, 0, H, H, fill=BG, outline=BG)
        c.create_oval(W - H, 0, W, H, fill=BG, outline=BG)
        c.create_rectangle(r, 0, W - r, H, fill=BG, outline=BG)
        self.dot = c.create_oval(18, H // 2 - 5, 28, H // 2 + 5,
                                 fill=LIME, outline=LIME)
        self.txt = c.create_text(38, H // 2, anchor="w", fill="white",
                                 font=("Segoe UI", 10, "bold"), text="")
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
        pass  # ponytail: no waveform in v1; animate the dot if you miss it

    def quit(self):
        self._want = "quit"  # _tick applies it on the tkinter thread

    # ---- applied on the tkinter thread via polling
    def _tick(self):
        if self._want == "quit":
            self.root.destroy()
            return
        if self._want != self._shown:
            self._shown = self._want
            if self._shown == "hidden":
                self.root.attributes("-alpha", 0.0)
            else:
                rec = self._shown == "recording"
                self.canvas.itemconfig(self.dot, fill=LIME if rec else "#777",
                                       outline=LIME if rec else "#777")
                self.canvas.itemconfig(
                    self.txt, text="Recording" if rec else "Processing…")
                self.root.attributes("-alpha", 1.0)
                self.root.attributes("-topmost", True)
        self.root.after(80, self._tick)

    def run(self):
        self.root.after(80, self._tick)
        self.root.mainloop()
