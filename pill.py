"""Pastilla flotante estilo Wispr Flow, paleta Higgsfield.

[X] [waveform / dots] [✓]: fondo #141414, outline lima #CCFF00 con glow.
Panel non-activating: nunca roba el foco del app donde estás escribiendo.
Thread-safe: show_*/hide se pueden llamar desde cualquier thread.
"""
import math
import time
from collections import deque

import objc
from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                    NSBackingStoreBuffered, NSBezierPath, NSColor, NSMenu,
                    NSMenuItem, NSPanel, NSScreen, NSShadow, NSStatusBar, NSView,
                    NSVariableStatusItemLength,
                    NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel)
from Foundation import NSObject
from PyObjCTools import AppHelper

# Paleta Higgsfield
LIME = NSColor.colorWithSRGBRed_green_blue_alpha_(0.80, 1.0, 0.0, 1.0)   # CCFF00
DARK = NSColor.colorWithSRGBRed_green_blue_alpha_(0.078, 0.078, 0.078, 0.97)  # 141414
GRAY = NSColor.colorWithSRGBRed_green_blue_alpha_(0.18, 0.18, 0.18, 1.0)
WHITE = NSColor.whiteColor()
INK = NSColor.colorWithSRGBRed_green_blue_alpha_(0.04, 0.04, 0.04, 1.0)

W, H = 170, 36
BTN_ZONE = 34          # ancho clickeable de cada extremo
N_BARS, N_DOTS = 11, 8

HIDDEN, RECORDING, PROCESSING = 0, 1, 2


def _fill_circle(cx, cy, r, color):
    color.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(((cx - r, cy - r), (2 * r, 2 * r))).fill()


class _PillView(NSView):
    def initWithPill_(self, pill):
        self = objc.super(_PillView, self).initWithFrame_(((0, 0), (W, H)))
        if self:
            self.pill = pill
        return self

    def acceptsFirstMouse_(self, event):
        return True  # el primer click actúa, sin activar la app

    def mouseDown_(self, event):
        x = self.convertPoint_fromView_(event.locationInWindow(), None).x
        if x < BTN_ZONE:
            self.pill.on_cancel()
        elif x > W - BTN_ZONE:
            self.pill.on_confirm()

    def drawRect_(self, rect):
        mode = self.pill.mode
        cy = H / 2.0

        # cuerpo: rounded-full oscuro con outline lima + glow
        body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((4, 4), (W - 8, H - 8)), (H - 8) / 2.0, (H - 8) / 2.0)
        DARK.setFill()
        body.fill()
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.80, 1.0, 0.0, 0.35).set()
        glow = NSShadow.alloc().init()
        glow.setShadowColor_(LIME.colorWithAlphaComponent_(0.8))
        glow.setShadowBlurRadius_(8.0)
        glow.setShadowOffset_((0, 0))
        glow.set()
        LIME.setStroke()
        body.setLineWidth_(1.7)
        body.stroke()
        NSShadow.alloc().init().set()  # apagar la sombra para el resto

        # botón X (izquierda): círculo gris, X blanca
        _fill_circle(20, cy, 10, GRAY)
        WHITE.setStroke()
        for dx, dy in ((1, 1), (1, -1)):
            p = NSBezierPath.bezierPath()
            p.setLineWidth_(1.8)
            p.setLineCapStyle_(1)  # round
            p.moveToPoint_((20 - 3.2 * dx, cy - 3.2 * dy))
            p.lineToPoint_((20 + 3.2 * dx, cy + 3.2 * dy))
            p.stroke()

        # botón ✓ (derecha): círculo lima, check oscuro (CTA Higgsfield)
        bx = W - 20
        _fill_circle(bx, cy, 10, LIME)
        INK.setStroke()
        p = NSBezierPath.bezierPath()
        p.setLineWidth_(2.0)
        p.setLineCapStyle_(1)
        p.setLineJoinStyle_(1)
        p.moveToPoint_((bx - 4, cy + 0.5))
        p.lineToPoint_((bx - 1, cy - 3))
        p.lineToPoint_((bx + 4.5, cy + 3.5))
        p.stroke()

        # zona central
        x0, x1 = BTN_ZONE + 4, W - BTN_ZONE - 4
        span = x1 - x0
        if mode == RECORDING:
            targets = list(self.pill.levels)  # newest primero = barra izquierda
            disp = self.pill.display
            step = span / float(N_BARS)
            bw = 2.5
            for i in range(N_BARS):
                tgt = targets[i] if i < len(targets) else 0.05
                disp[i] += (tgt - disp[i]) * 0.35  # ease: persigue el objetivo
                h = 3 + disp[i] * 14
                # fade sutil hacia los bordes (más denso al centro = look premium)
                edge = 1.0 - 0.5 * abs(i - (N_BARS - 1) / 2) / ((N_BARS - 1) / 2)
                bx_ = x0 + step * i + (step - bw) / 2
                bar = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    ((bx_, cy - h / 2), (bw, h)), bw / 2, bw / 2)
                WHITE.colorWithAlphaComponent_(0.5 + 0.5 * edge).setFill()
                bar.fill()
        elif mode == PROCESSING:
            t = time.monotonic()
            step = span / float(N_DOTS)
            for i in range(N_DOTS):
                alpha = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(t * 6 - i * 0.7))
                _fill_circle(x0 + step * i + step / 2, cy, 1.8,
                             WHITE.colorWithAlphaComponent_(alpha))


class Pill:
    """API thread-safe. on_cancel/on_confirm se llaman en el main thread."""

    def __init__(self, on_cancel=lambda: None, on_confirm=lambda: None):
        self.mode = HIDDEN
        self.levels = deque([0.05] * N_BARS, maxlen=N_BARS)
        self.display = [0.05] * N_BARS  # alturas animadas (easing hacia levels)
        self.on_cancel = on_cancel
        self.on_confirm = on_confirm
        self._timer = None

        screen = NSScreen.mainScreen().frame()
        x = (screen.size.width - W) / 2.0
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((x, 70), (W, H)),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered, False)
        panel.setLevel_(25)  # NSStatusWindowLevel: sobre todo
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.view = _PillView.alloc().initWithPill_(self)
        panel.setContentView_(self.view)
        self.panel = panel

    # ---- llamables desde cualquier thread ----
    def push_level(self, v):
        # appendleft: lo nuevo entra por la izquierda y fluye hacia la derecha
        self.levels.appendleft(max(0.03, min(1.0, v)))

    def show_recording(self):
        AppHelper.callAfter(self._set_mode, RECORDING)

    def show_processing(self):
        AppHelper.callAfter(self._set_mode, PROCESSING)

    def hide(self):
        AppHelper.callAfter(self._set_mode, HIDDEN)

    # ---- main thread ----
    def _set_mode(self, mode):
        self.mode = mode
        if mode == HIDDEN:
            self.panel.orderOut_(None)
            if self._timer:
                self._timer.invalidate()
                self._timer = None
        else:
            self.panel.orderFrontRegardless()
            if not self._timer:
                from Foundation import NSTimer
                self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                    1 / 30.0, True, lambda t: self.view.setNeedsDisplay_(True))


def make_app():
    """NSApplication accessory (sin ícono en el Dock)."""
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    return app


class _MenuTarget(NSObject):
    def quit_(self, sender):
        NSApplication.sharedApplication().terminate_(None)

    def settings_(self, sender):
        import webbrowser
        webbrowser.open("http://127.0.0.1:8091")


_menubar_refs = []  # el status item muere si el GC lo recoge


def make_menubar():
    """Ícono 🎙 en la barra de menú con estado y Salir."""
    item = NSStatusBar.systemStatusBar().statusItemWithLength_(
        NSVariableStatusItemLength)
    item.button().setTitle_("🎙")
    target = _MenuTarget.alloc().init()
    menu = NSMenu.alloc().init()
    info = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Orac Voice · Local dictation", None, "")
    info.setEnabled_(False)
    menu.addItem_(info)
    settings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Settings & History…", "settings:", ",")
    settings_item.setTarget_(target)
    menu.addItem_(settings_item)
    menu.addItem_(NSMenuItem.separatorItem())
    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit Orac Voice", "quit:", "q")
    quit_item.setTarget_(target)
    menu.addItem_(quit_item)
    item.setMenu_(menu)
    _menubar_refs.extend([item, target, menu])
    return item


if __name__ == "__main__":
    # demo standalone: grabando 4s (waveform falsa) -> procesando 2.5s -> fin
    import threading

    app = make_app()
    pill = Pill(on_cancel=lambda: print("click X"),
                on_confirm=lambda: print("click ✓"))

    def fake_levels():
        t0 = time.monotonic()
        while time.monotonic() - t0 < 4:
            t = time.monotonic()
            pill.push_level(0.3 + 0.7 * abs(math.sin(t * 8)) * (0.4 + 0.6 * math.sin(t * 1.7) ** 2))
            time.sleep(0.05)
        pill.show_processing()
        time.sleep(2.5)
        pill.hide()
        time.sleep(0.3)
        AppHelper.callAfter(app.terminate_, None)

    pill.show_recording()
    threading.Thread(target=fake_levels, daemon=True).start()
    AppHelper.runEventLoop()
