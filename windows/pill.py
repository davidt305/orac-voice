"""Pastilla visual de Orac Voice para Windows (tkinter, stdlib).

Solo indicador, sin botones: Escape cancela y la tecla de dictado confirma.
La ventana es click-through y nunca roba el foco (WS_EX_NOACTIVATE +
WS_EX_TRANSPARENT), así el Ctrl+V siempre llega al campo de texto del usuario.
Misma interfaz que pill.py de Mac: show_recording / show_processing / hide /
push_level, más run() que corre el mainloop en el thread principal.
"""
import ctypes
import tkinter as tk

LIME = "#ccff00"
BG = "#0a0a0a"
CHROMA = "#010101"  # color reservado para transparencia (nunca se dibuja)
W, H = 170, 36

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080


class Pill:
    def __init__(self):
        self._want = "hidden"   # escrito desde otros threads; leer str es atómico
        self._shown = None
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.0)  # oculta sin unmap (conserva estilos)
        self.root.attributes("-transparentcolor", CHROMA)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw - W) // 2}+{sh - H - 60}")
        c = tk.Canvas(self.root, width=W, height=H, bg=CHROMA,
                      highlightthickness=0)
        c.pack()
        r = H // 2  # cápsula: dos círculos + rectángulo
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
        """No roba foco, deja pasar los clicks y no aparece en Alt+Tab."""
        u = ctypes.windll.user32
        hwnd = u.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE
                         | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)

    # ---- API llamada desde los threads del daemon (solo setea el estado)
    def show_recording(self):
        self._want = "recording"

    def show_processing(self):
        self._want = "processing"

    def hide(self):
        self._want = "hidden"

    def push_level(self, level):
        pass  # ponytail: sin waveform en v1; animar el dot si se echa de menos

    def quit(self):
        self._want = "quit"  # el _tick lo aplica en el thread de tkinter

    # ---- aplicado en el thread de tkinter vía polling
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
