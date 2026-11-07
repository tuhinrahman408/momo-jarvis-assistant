"""The Momo window - JARVIS x modern AI dashboard (PySide6).

 top      : logo, online status, AI engine, window buttons (minimise / maximise / close)
 left     : New chat, History, Memory, Settings
 centre   : big animated AI orb + system panel (CPU, RAM, internet)
 right    : conversation (or history / memory / settings) and, at the bottom, the input bar
 bottom   : keyboard button, text box, voice button, send button

Selecting a mode says one sentence once: keyboard -> "Sir, your command."  voice -> "Sir, I am listening to you."
Closing the window stops the program, but a task that is already running is finished first
(a message being sent, a call, ...). Browser tabs and opened apps are never closed by Momo.
"""
import math
import os
import queue
import random
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import config, console, settings, tts
from .errors import log_dir, log_exception, short_reason
from .orchestrator import FAILED, GREETING, LISTENING
from .skills import store

STATE_COLORS = {          # (light, deep)
    "idle": ("#4cc9f0", "#4361ee"),
    "listening": ("#80ffdb", "#14b8a6"),
    "thinking": ("#c77dff", "#7b2cbf"),
    "working": ("#ffcf56", "#fb5607"),
    "speaking": ("#ff7ab8", "#b5179e"),
}
STATE_TEXT = {"idle": "Ready", "listening": "Listening...", "thinking": "Thinking...",
              "working": "Working...", "speaking": "Speaking..."}
SPEED = {"idle": 0.9, "listening": 1.7, "thinking": 3.0, "working": 3.8, "speaking": 2.6}
AMP = {"idle": 0.03, "listening": 0.06, "thinking": 0.05, "working": 0.04, "speaking": 0.10}


def rgba(color, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


def lerp_color(a: QColor, b: QColor, k: float) -> QColor:
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * k,
                           a.greenF() + (b.greenF() - a.greenF()) * k,
                           a.blueF() + (b.blueF() - a.blueF()) * k)


# =============================== widgets ===============================
class Orb(QWidget):
    """The big animated AI core. Its colour and motion show what Momo is doing: a ring of bars dances
    while speaking or listening and sweeps around while thinking or working. HUD rings turn around it."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(390, 390)
        self.state = "idle"
        self.speaking = False
        self.t = 0.0
        self.c1 = QColor(STATE_COLORS["idle"][0])
        self.c2 = QColor(STATE_COLORS["idle"][1])
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    @property
    def key(self) -> str:
        return "speaking" if self.speaking else self.state

    def _tick(self):
        self.t += 0.016
        light, deep = STATE_COLORS[self.key]
        self.c1 = lerp_color(self.c1, QColor(light), 0.08)
        self.c2 = lerp_color(self.c2, QColor(deep), 0.08)
        self.update()

    def _bar_amp(self, key, i, n, t):
        ang = i * 2 * math.pi / n
        if key == "speaking":
            return 0.30 + 0.70 * abs(math.sin(t * 5 + i * 0.9) * math.sin(t * 2.3 + i * 0.37))
        if key == "listening":
            return 0.18 + 0.55 * abs(math.sin(t * 3 + i * 0.6))
        if key in ("thinking", "working"):
            speed = 3.2 if key == "thinking" else 5.0
            return 0.10 + 0.85 * max(0.0, math.cos(ang - t * speed)) ** 6
        return 0.10 + 0.05 * math.sin(t + i * 0.4)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        key, t = self.key, self.t
        sp = SPEED[key]
        wobble = math.sin(t * sp) + (0.5 * math.sin(t * sp * 2.3) if key == "speaking" else 0)
        base = min(w, h) * 0.165 * (1 + AMP[key] * wobble)
        c1, c2 = self.c1, self.c2
        p.setPen(Qt.NoPen)

        reach = min(w, h) / 2 - 1  # the glow fades out before the edge of the widget
        glow = QRadialGradient(cx, cy, reach)
        glow.setColorAt(0.0, rgba(c1, 135))
        glow.setColorAt(0.36, rgba(c2, 62))
        glow.setColorAt(1.0, rgba(c2, 0))
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), reach, reach)

        if key in ("listening", "speaking"):  # expanding ripples
            for i in range(3):
                phase = (t * 0.55 + i / 3) % 1
                r = base * (1.55 + phase * 1.0)
                p.setPen(QPen(rgba(c1, int(110 * (1 - phase))), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(cx, cy), r, r)

        n = 72  # radial equaliser ring
        inner = base * 1.5
        for i in range(n):
            ang = i * 2 * math.pi / n - math.pi / 2
            amp = self._bar_amp(key, i, n, t)
            length = 3 + amp * 22
            p.setPen(QPen(rgba(c1, 80 + int(160 * amp)), 2.8, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx + inner * math.cos(ang), cy + inner * math.sin(ang)),
                       QPointF(cx + (inner + length) * math.cos(ang), cy + (inner + length) * math.sin(ang)))

        # HUD tick ring (turns slowly)
        r_tick = base * 2.02
        spin = t * 0.12 * sp
        for i in range(90):
            ang = spin + i * 2 * math.pi / 90
            long_tick = i % 5 == 0
            l0, l1 = r_tick, r_tick + (9 if long_tick else 4.5)
            p.setPen(QPen(rgba(c1, 200 if long_tick else 95), 1.6 if long_tick else 1.0))
            p.drawLine(QPointF(cx + l0 * math.cos(ang), cy + l0 * math.sin(ang)),
                       QPointF(cx + l1 * math.cos(ang), cy + l1 * math.sin(ang)))

        # dashed ring (turns the other way)
        pen = QPen(rgba(c1, 120), 1.4)
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([2.0, 7.0])
        pen.setDashOffset(-t * 9 * sp)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r_d = base * 2.38
        p.drawEllipse(QPointF(cx, cy), r_d, r_d)

        for i in range(2):  # segmented outer arcs
            r = base * (2.62 + 0.14 * i)
            p.setPen(QPen(rgba(c1, 190 - i * 70), 2.2, Qt.SolidLine, Qt.RoundCap))
            start = int((t * 28 * sp * (1 if i == 0 else -1) + i * 160) * 16)
            p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), start, 62 * 16)
            p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), start + 180 * 16, 38 * 16)

        p.setPen(Qt.NoPen)  # orbiting dots
        for i in range(5):
            ang = t * 0.7 * sp + i * (2 * math.pi / 5)
            r = base * 2.92
            p.setBrush(rgba(c1, 210))
            p.drawEllipse(QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang)), 2.8, 2.8)

        core = QRadialGradient(cx - base * 0.3, cy - base * 0.4, base * 1.5)
        core.setColorAt(0.0, QColor(255, 255, 255, 245))
        core.setColorAt(0.22, c1)
        core.setColorAt(1.0, c2)
        p.setBrush(core)
        p.drawEllipse(QPointF(cx, cy), base, base)

        rim = QRadialGradient(cx, cy, base)
        rim.setColorAt(0.78, rgba(c1, 0))
        rim.setColorAt(1.0, rgba("#ffffff", 80))
        p.setBrush(rim)
        p.drawEllipse(QPointF(cx, cy), base, base)

        p.setBrush(Qt.NoBrush)  # thin inner reactor ring
        p.setPen(QPen(rgba("#ffffff", 70), 1.2))
        p.drawEllipse(QPointF(cx, cy), base * 0.62, base * 0.62)

        sheen = QRadialGradient(cx - base * 0.35, cy - base * 0.5, base * 0.7)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 130))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(sheen)
        p.drawEllipse(QPointF(cx - base * 0.3, cy - base * 0.4), base * 0.6, base * 0.45)


class LogoMark(QWidget):
    """Small Momo logo at the top of the button rail."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(44, 44)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(22, 22)
        g = QLinearGradient(0, 0, 44, 44)
        g.setColorAt(0, QColor("#4cc9f0"))
        g.setColorAt(0.5, QColor("#7b2cbf"))
        g.setColorAt(1, QColor("#ff7ab8"))
        p.setPen(QPen(g, 2.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, 16, 16)
        core = QRadialGradient(QPointF(18, 17), 14)
        core.setColorAt(0, QColor(255, 255, 255, 240))
        core.setColorAt(0.3, QColor("#4cc9f0"))
        core.setColorAt(1, QColor("#7b2cbf"))
        p.setPen(Qt.NoPen)
        p.setBrush(core)
        p.drawEllipse(c, 8, 8)


class Particles:
    """Slow floating light dots drawn behind everything."""

    def __init__(self, n=48):
        rnd = random.Random(11)
        self.items = [(rnd.random(), rnd.random(), rnd.uniform(1.0, 2.6), rnd.uniform(0.004, 0.016),
                       rnd.uniform(0, 6.28)) for _ in range(n)]

    def paint(self, p, w, h, t, color):
        p.setPen(Qt.NoPen)
        for x0, y0, r, speed, phase in self.items:
            y = (y0 - t * speed) % 1
            x = x0 + 0.012 * math.sin(t * 0.5 + phase)
            alpha = 25 + int(70 * (0.5 + 0.5 * math.sin(t * 0.9 + phase)))
            p.setBrush(rgba(color, alpha))
            p.drawEllipse(QPointF(x * w, y * h), r, r)


class VoiceBars(QWidget):
    """Equaliser bars shown instead of the text box while the voice mode is selected."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(54)
        self.active = False
        self.t = 0.0
        self.color = QColor(STATE_COLORS["listening"][0])
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

    def _tick(self):
        self.t += 0.033
        if self.isVisible():
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n, gap = 42, 5
        bw = (self.width() - 40 - (n - 1) * gap) / n
        mid = self.height() / 2
        for i in range(n):
            if self.active:
                amp = (0.5 + 0.5 * math.sin(self.t * 5 + i * 0.55)) * (0.55 + 0.45 * math.sin(self.t * 2.1 + i * 0.21))
            else:
                amp = 0.08
            hh = 4 + amp * (self.height() - 14)
            x = 20 + i * (bw + gap)
            p.setPen(Qt.NoPen)
            p.setBrush(rgba(self.color, 230 if self.active else 90))
            p.drawRoundedRect(QRectF(x, mid - hh / 2, bw, hh), bw / 2, bw / 2)
            
# ---- vector icons (drawn in a 24 x 24 box centred on 0,0) ----
def draw_icon(p, kind):
    if kind == "keyboard":
        p.drawRoundedRect(QRectF(-11, -7, 22, 14), 3, 3)
        keep = p.pen()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        for row, xs in ((-3.2, (-6.6, -2.2, 2.2, 6.6)), (0.4, (-4.4, 0, 4.4))):
            for x in xs:
                p.drawRoundedRect(QRectF(x - 1.2, row - 1, 2.4, 2), 0.6, 0.6)
        p.drawRoundedRect(QRectF(-5.5, 3.2, 11, 1.9), 0.9, 0.9)
        p.setPen(keep)
        p.setBrush(Qt.NoBrush)
    elif kind == "mic":
        p.drawRoundedRect(QRectF(-3.6, -10.5, 7.2, 13), 3.6, 3.6)
        path = QPainterPath()
        path.moveTo(-7, -1.5)
        path.arcTo(QRectF(-7, -7, 14, 14), 180, 180)
        p.drawPath(path)
        p.drawLine(QPointF(0, 7), QPointF(0, 10.5))
        p.drawLine(QPointF(-4, 10.5), QPointF(4, 10.5))
    elif kind == "send":
        path = QPainterPath()
        path.moveTo(-9, -8)
        path.lineTo(10, 0)
        path.lineTo(-9, 8)
        path.lineTo(-5, 0)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "close":
        p.drawLine(QPointF(-6.5, -6.5), QPointF(6.5, 6.5))
        p.drawLine(QPointF(-6.5, 6.5), QPointF(6.5, -6.5))
    elif kind == "min":
        p.drawLine(QPointF(-6.5, 0), QPointF(6.5, 0))
    elif kind == "max":
        p.drawRoundedRect(QRectF(-6, -6, 12, 12), 2, 2)
    elif kind == "plus":
        p.drawLine(QPointF(-7.5, 0), QPointF(7.5, 0))
        p.drawLine(QPointF(0, -7.5), QPointF(0, 7.5))
    elif kind == "clock":
        p.drawEllipse(QPointF(0, 0), 9.5, 9.5)
        p.drawLine(QPointF(0, -5.5), QPointF(0, 0))
        p.drawLine(QPointF(0, 0), QPointF(4.5, 3))
    elif kind == "chip":
        p.drawRoundedRect(QRectF(-6, -6, 12, 12), 2.5, 2.5)
        p.drawRect(QRectF(-2.4, -2.4, 4.8, 4.8))
        for v in (-3.5, 0, 3.5):
            p.drawLine(QPointF(-9.5, v), QPointF(-6, v))
            p.drawLine(QPointF(6, v), QPointF(9.5, v))
            p.drawLine(QPointF(v, -9.5), QPointF(v, -6))
            p.drawLine(QPointF(v, 6), QPointF(v, 9.5))
    elif kind == "gear":
        p.drawEllipse(QPointF(0, 0), 6.2, 6.2)
        p.drawEllipse(QPointF(0, 0), 2.6, 2.6)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(6.2 * math.cos(a), 6.2 * math.sin(a)), QPointF(9.4 * math.cos(a), 9.4 * math.sin(a)))


class IconButton(QAbstractButton):
    """A round glass button with a hand-drawn vector icon."""

    def __init__(self, kind: str, tip: str, accent=("#4cc9f0", "#4361ee"), size=62):
        super().__init__()
        self.kind, self.accent = kind, accent
        self.active, self.hover = False, False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)

    def set_active(self, value: bool):
        self.active = value
        self.update()

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        c = QPointF(w / 2, h / 2)
        r = w / 2 - 3
        light, deep = self.accent
        if self.active and self.kind == "mic":  # pulsing ring while the voice mode is on
            phase = (time.time() * 0.9) % 1
            p.setPen(QPen(rgba(light, int(160 * (1 - phase))), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, r + phase * 7, r + phase * 7)
        if self.active or self.hover:
            glow = QRadialGradient(c, w / 2)
            glow.setColorAt(0.55, rgba(light, 110 if self.active else 55))
            glow.setColorAt(1.0, rgba(light, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, w / 2, w / 2)
        if self.active:
            g = QLinearGradient(0, 0, w, h)
            g.setColorAt(0, QColor(light))
            g.setColorAt(1, QColor(deep))
            p.setBrush(g)
            p.setPen(QPen(rgba("#ffffff", 90), 1))
        else:
            p.setBrush(rgba("#ffffff", 34 if self.hover else 16))
            p.setPen(QPen(rgba("#ffffff", 46), 1))
        p.drawEllipse(c, r, r)

        p.translate(c)
        k = w / 62 * 1.15 if w >= 56 else w / 44 * 0.95
        p.scale(k, k)
        p.setPen(QPen(QColor("#ffffff") if (self.active or self.hover) else rgba("#dfe6ff", 215), 2.1,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        draw_icon(p, self.kind)


class NavButton(QAbstractButton):
    """Sidebar item: icon + label, with a glowing bar on the left while its page is open."""

    def __init__(self, kind: str, label: str):
        super().__init__()
        self.kind, self.label = kind, label
        self.active, self.hover = False, False
        self.setFixedHeight(52)
        self.setCursor(Qt.PointingHandCursor)

    def set_active(self, value: bool):
        self.active = value
        self.update()

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        box = QRectF(2, 2, w - 4, h - 4)
        if self.active or self.hover:
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0, rgba("#4cc9f0", 70 if self.active else 30))
            g.setColorAt(1, rgba("#4361ee", 14 if self.active else 6))
            path = QPainterPath()
            path.addRoundedRect(box, 14, 14)
            p.fillPath(path, g)
            p.setPen(QPen(rgba("#4cc9f0", 90 if self.active else 40), 1))
            p.drawPath(path)
        if self.active:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#4cc9f0"))
            p.drawRoundedRect(QRectF(2, 13, 3.5, h - 26), 1.7, 1.7)
        p.save()
        p.translate(34, h / 2)
        p.scale(0.95, 0.95)
        p.setPen(QPen(QColor("#ffffff") if (self.active or self.hover) else rgba("#b9c4f5", 220), 1.9,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        draw_icon(p, self.kind)
        p.restore()
        f = QFont("Segoe UI", 10)
        f.setWeight(QFont.DemiBold if self.active else QFont.Normal)
        p.setFont(f)
        p.setPen(QColor("#ffffff") if self.active else rgba("#b9c4f5", 230))
        p.drawText(QRectF(58, 0, w - 64, h), Qt.AlignVCenter | Qt.AlignLeft, self.label)


class HudPanel(QFrame):
    """Dark glass panel with cyan corner brackets (the HUD look)."""

    def __init__(self, radius=20, alpha=150):
        super().__init__()
        self.radius, self.alpha = radius, alpha

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(14, 28, 62, self.alpha))
        g.setColorAt(1, QColor(8, 16, 40, self.alpha))
        p.fillPath(path, g)
        p.setPen(QPen(rgba("#4cc9f0", 38), 1))
        p.drawPath(path)
        p.setPen(QPen(rgba("#4cc9f0", 210), 2, Qt.SolidLine, Qt.SquareCap))
        n, x0, y0, x1, y1 = 15, r.left() + 4, r.top() + 4, r.right() - 4, r.bottom() - 4
        for x, y, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * n, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * n))


class Gauge(QWidget):
    """Round gauge for CPU / RAM / internet."""

    def __init__(self, title: str):
        super().__init__()
        self.title, self.frac, self.text = title, 0.0, "--"
        self.color = QColor("#4cc9f0")
        self.setFixedSize(104, 112)

    def set_value(self, frac, text, color="#4cc9f0"):
        self.frac, self.text, self.color = max(0.0, min(1.0, frac)), text, QColor(color)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(14, 8, 76, 76)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(rgba("#ffffff", 28), 6, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, 225 * 16, -270 * 16)
        if self.frac > 0:
            p.setPen(QPen(self.color, 6, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, 225 * 16, int(-270 * self.frac * 16))
        f = QFont("Consolas", 13)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(rect, Qt.AlignCenter, self.text)
        sf = QFont("Segoe UI", 8)
        sf.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        p.setFont(sf)
        p.setPen(rgba("#8f9bd6", 230))
        p.drawText(QRectF(0, 88, self.width(), 20), Qt.AlignHCenter | Qt.AlignTop, self.title)


class Switch(QAbstractButton):
    """On / off toggle."""

    def __init__(self, on=False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(on)
        self.setFixedSize(50, 28)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        p.setPen(QPen(rgba("#4cc9f0" if on else "#ffffff", 150 if on else 50), 1))
        p.setBrush(rgba("#4cc9f0", 90) if on else rgba("#ffffff", 20))
        p.drawRoundedRect(QRectF(1, 1, 48, 26), 13, 13)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff") if on else rgba("#cfd6ff", 200))
        p.drawEllipse(QPointF(37 if on else 13, 14), 9, 9)


class NetProbe(threading.Thread):
    """Checks the internet every few seconds (never blocks the window)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.ok, self.ms = None, 0

    def run(self):
        while True:
            start = time.time()
            try:
                socket.create_connection(("1.1.1.1", 53), timeout=2.5).close()
                self.ok, self.ms = True, int((time.time() - start) * 1000)
            except OSError:
                self.ok, self.ms = False, 0
            time.sleep(6)
   
    BUBBLE_STYLE = {
    "user": ("background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #4361ee, stop:1 #7209b7);"
             "color:#ffffff; border-radius:18px; border-bottom-right-radius:5px;"),
    "momo": ("background: rgba(255,255,255,0.09); color:#eef1ff; border-radius:18px;"
             "border-bottom-left-radius:5px; border:1px solid rgba(255,255,255,0.10);"),
    "status": ("background: rgba(76,201,240,0.12); color:#9be7ff; border-radius:14px;"
               "border:1px solid rgba(76,201,240,0.30);"),
    "error": ("background: rgba(255,77,109,0.15); color:#ffb3c1; border-radius:14px;"
              "border:1px solid rgba(255,77,109,0.40);"),
    "confirm": ("background: rgba(255,196,61,0.12); color:#ffe29a; border-radius:16px;"
                "border:1px solid rgba(255,196,61,0.45);"),
}
PILL_STYLE = ("QPushButton { background: rgba(255,255,255,0.07); color:#cfd6ff; border:1px solid rgba(255,255,255,0.12);"
              "border-radius:15px; padding:6px 12px; font-size:12px; }"
              "QPushButton:hover { background: rgba(255,255,255,0.16); color:#ffffff; }")

BUBBLE_STYLE = {
    "user": ("background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #4361ee, stop:1 #7209b7);"
             "color:#ffffff; border-radius:18px; border-bottom-right-radius:5px;"),
    "momo": ("background: rgba(255,255,255,0.09); color:#eef1ff; border-radius:18px;"
             "border-bottom-left-radius:5px; border:1px solid rgba(255,255,255,0.10);"),
    "status": ("background: rgba(76,201,240,0.12); color:#9be7ff; border-radius:14px;"
               "border:1px solid rgba(76,201,240,0.30);"),
    "error": ("background: rgba(255,77,109,0.15); color:#ffb3c1; border-radius:14px;"
              "border:1px solid rgba(255,77,109,0.40);"),
    "confirm": ("background: rgba(255,196,61,0.12); color:#ffe29a; border-radius:16px;"
                "border:1px solid rgba(255,196,61,0.45);"),
}

PILL_STYLE = ("QPushButton { background: rgba(255,255,255,0.07); color:#cfd6ff; border:1px solid rgba(255,255,255,0.12);"
              "border-radius:15px; padding:6px 12px; font-size:12px; }"
              "QPushButton:hover { background: rgba(255,255,255,0.16); color:#ffffff; }")



def fade_in(widget, ms=260):
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    anim.start()
    widget._fade = anim


def make_bubble(kind: str, text: str, buttons=None) -> QWidget:
    """A chat bubble with a small time stamp. `buttons` = [(label, callback, style)] for confirmations."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    col = QVBoxLayout()
    col.setSpacing(3)
    col.setContentsMargins(0, 0, 0, 0)
    label = QLabel(text)
    label.setWordWrap(True)
    font = label.font()
    font.setPixelSize(14)
    label.setFont(font)
    fm = label.fontMetrics()
    text_w = fm.boundingRect(0, 0, 470, 10000, Qt.TextWordWrap, text).width()
    label.setFixedWidth(min(470, text_w + 46))  # padding included; long texts wrap at 470
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    
    label.setStyleSheet(BUBBLE_STYLE.get(kind, BUBBLE_STYLE["momo"]) + "padding:10px 15px;")
    stamp = QLabel(datetime.now().strftime("%I:%M %p"))
    stamp.setStyleSheet("color:#5f6a9c; font-size:10px; padding:0 6px;")
    align = Qt.AlignRight if kind == "user" else Qt.AlignLeft
    col.addWidget(label, 0, align)
    if buttons:
        brow = QHBoxLayout()
        brow.setContentsMargins(4, 4, 0, 2)
        brow.setSpacing(10)
        row.buttons = []
        for text_, callback, style in buttons:
            b = QPushButton(text_)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(34)
            b.setStyleSheet(style)
            b.clicked.connect(callback)
            brow.addWidget(b)
            row.buttons.append(b)
        brow.addStretch(1)
        col.addLayout(brow)
    col.addWidget(stamp, 0, align)
    if kind == "user":
        lay.addStretch(1)
        lay.addLayout(col)
    else:
        lay.addLayout(col)
        lay.addStretch(1)
    return row


YES_STYLE = ("QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #34d399, stop:1 #14b8a6);"
             "color:#06281f; border:none; border-radius:17px; padding:0 22px; font-weight:700; }"
             "QPushButton:hover { background:#5eead4; } QPushButton:disabled { background:#2a3a4a; color:#6b7a8c; }")
NO_STYLE = ("QPushButton { background: rgba(255,255,255,0.10); color:#ffd1d9; border:1px solid rgba(255,122,144,0.5);"
            "border-radius:17px; padding:0 22px; font-weight:700; }"
            "QPushButton:hover { background: rgba(255,122,144,0.25); } QPushButton:disabled { color:#6b7a8c; border-color:#2a3a4a; }")


class Glass(QFrame):
    def __init__(self, radius=30, alpha=0.05):
        super().__init__()
        self.setStyleSheet(f"Glass {{ background: rgba(255,255,255,{alpha}); border-radius:{radius}px;"
                           f"border:1px solid rgba(255,255,255,0.08); }}")


# =============================== bridge + worker ===============================
class Bridge(QObject):
    message = Signal(str, str)   # kind, text
    state = Signal(str)          # idle / listening / thinking / working
    speaking = Signal(bool)
    mode = Signal(str)           # keyboard / voice


class Assistant(threading.Thread):
    """Runs Momo in its own thread. It is not a daemon: when the window is closed the program
    waits here until the task that is currently running has finished."""

    def __init__(self, bridge: Bridge):
        super().__init__(name="momo-worker")
        self.bridge = bridge
        self.events = queue.Queue()      # ("text", str) | ("mode", str) | ("quit", None)
        self.mode = "keyboard"
        self.closing = False
        self.detached = False            # True once the window is gone
        self.busy = False
        self.voice = None
        self.voice_errors = 0
        self.momo = None

    # ----- called from the window (UI thread) -----
    def post(self, kind, value=None):
        self.events.put((kind, value))

    # ----- helpers (worker thread) -----
    def _emit(self, signal, *args):
        if not self.detached:
            signal.emit(*args)

    def _sink(self, kind, text):
        if self.detached:
            console.show(text, kind)   # the window is gone: show the rest in the terminal
        else:
            self.bridge.message.emit(kind, text)

    def _set_mode(self, mode):
        from .speech import speaker
        if mode == self.mode:
            return
        self.mode = mode
        self._emit(self.bridge.mode, mode)
        if mode == "voice":
            tts.say(LISTENING, kind="status")
            speaker.wait(20)
        else:
            tts.say(GREETING, kind="status")
            self._emit(self.bridge.state, "idle")

    def _voice_once(self):
        from .speech import speaker
        try:
            if self.voice is None:
                from .stt import VoiceListener
                self.voice = VoiceListener()
            speaker.wait(30)  # never listen to Momo's own voice
            self._emit(self.bridge.state, "listening")
            text = self.voice.listen(timeout=4)
            self.voice_errors = 0
        except Exception as e:
            log_exception(e, "voice input")
            self.voice_errors += 1
            if self.voice_errors >= 2:
                self.voice_errors = 0
                tts.say(f"{FAILED} Reason: voice input is not working ({short_reason(e)}).", kind="error")
                self._set_mode("keyboard")
            time.sleep(0.5)
            return None
        if text:
            self._emit(self.bridge.message, "user", text)
        return text

    def listen(self, prompt=""):
        """Next command (typed or spoken). Used by the main loop and for confirmations."""
        while not self.closing:
            try:
                kind, value = self.events.get(timeout=0 if self.mode == "voice" else 0.3)
            except queue.Empty:
                if self.mode == "voice":
                    text = self._voice_once()
                    if text:
                        return text
                continue
            if kind == "text":
                return value
            if kind == "mode":
                self._set_mode(value)
            elif kind == "newchat":
                if self.momo is not None:
                    self.momo.history = []
            elif kind == "quit":
                self.closing = True
        return None

    # ----- thread body -----
    def run(self):
        from .executor.browser import Browser
        from .orchestrator import Momo
        from .speech import speaker
        tts.set_sink(self._sink)
        speaker.callbacks.append(lambda v: self._emit(self.bridge.speaking, v))
        speaker.problem_callbacks.append(
            lambda text: self._sink("error", f"Voice output problem: {text}. Run python run.py --test-voice"))
        browser = Browser()  # created here: the browser must be used from this thread only
        self.momo = Momo(self, browser)

        def on_state(state):
            self.busy = state != "idle"
            self._emit(self.bridge.state, state)

        self.momo.on_state = on_state
        self.momo.start_services()
        try:
            tts.say(GREETING, kind="status")
            while not self.closing:
                text = self.listen()
                if text:
                    self.momo.process(text)  # never raises
        except Exception as e:
            log_exception(e, "worker")
        finally:
            self.momo.reminders.stop()
            browser.close()  # only disconnects: the Edge window and its tabs stay open
            speaker.wait(20)
            
# =============================== main window ===============================
def level_color(percent: float) -> str:
    return "#4cc9f0" if percent < 70 else ("#ffcf56" if percent < 90 else "#ff5d73")


def pill_style(color: str) -> str:
    c = QColor(color)
    border = f"rgba({c.red()},{c.green()},{c.blue()},0.40)"
    return (f"color:{color}; background: rgba(255,255,255,0.05); border:1px solid {border};"
            "border-radius:14px; padding:5px 14px; font-size:11px; letter-spacing:1px; font-weight:600;")


ROW_STYLE = ("QPushButton { background: rgba(255,255,255,0.05); color:#e6eaff; border:1px solid rgba(255,255,255,0.08);"
             "border-radius:12px; padding:11px 16px; text-align:left; font-size:13px; }"
             "QPushButton:hover { background: rgba(76,201,240,0.16); border-color: rgba(76,201,240,0.5); }")
SEGMENT_STYLE = ("QPushButton { background: rgba(255,255,255,0.06); color:#cfd6ff; border:1px solid rgba(255,255,255,0.12);"
                 "border-radius:14px; padding:7px 11px; font-size:11px; }"
                 "QPushButton:hover { background: rgba(255,255,255,0.14); }"
                 "QPushButton:checked { background: rgba(76,201,240,0.28); border-color:#4cc9f0; color:#ffffff; }")


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color:#6f7bb3; font-size:11px; letter-spacing:3px;")
    return label


class MomoWindow(QWidget):
    PAGES = {"chat": 0, "history": 1, "memory": 2, "settings": 3}
    TITLES = {"chat": "CONVERSATION", "history": "HISTORY", "memory": "MEMORY", "settings": "SETTINGS"}

    def __init__(self, bridge: Bridge, assistant: Assistant = None):
        super().__init__()
        self.bridge, self.assistant = bridge, assistant
        self.mode = "keyboard"
        self.page = "chat"
        self.drag = None
        self.confirm_row = None
        self.setWindowTitle("Momo")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(1280, 800)
        self.setMinimumSize(1120, 740)
        self.setStyleSheet("""
            QLabel { color:#e8ecff; background: transparent; }
            QLineEdit { background: transparent; border:none; color:#ffffff; font-size:15px;
                        selection-background-color:#4361ee; }
            QScrollArea { background: transparent; border:none; }
            QScrollBar:vertical { background: transparent; width:8px; margin:4px; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,0.20); border-radius:4px; min-height:30px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            QToolTip { background:#101a3a; color:#ffffff; border:1px solid #4cc9f0; padding:4px 8px; }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 22)
        outer.setSpacing(14)
        outer.addWidget(self._build_top())
        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_centre())
        body.addWidget(self._build_right(), 1)
        outer.addLayout(body, 1)

        bridge.message.connect(self.add_message)
        bridge.state.connect(self.set_state)
        bridge.speaking.connect(self.set_speaking)
        bridge.mode.connect(self.apply_mode)

        self.particles = Particles()
        self.t0 = time.time()
        self.net = NetProbe()
        self.net.start()
        self.bg_timer = QTimer(self)
        self.bg_timer.timeout.connect(self._frame)
        self.bg_timer.start(33)
        self.slow_timer = QTimer(self)
        self.slow_timer.timeout.connect(self._slow_tick)
        self.slow_timer.start(1500)
        self._slow_tick()
        self.show_page("chat")
        self.entry.setFocus()

    # ---------------- building blocks ----------------
    def _build_top(self) -> QWidget:
        self.top = QWidget()
        self.top.setFixedHeight(58)
        row = QHBoxLayout(self.top)
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(14)
        row.addWidget(LogoMark())
        box = QVBoxLayout()
        box.setSpacing(0)
        title = QLabel("MOMO")
        f = QFont("Segoe UI", 19, QFont.Bold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 7)
        title.setFont(f)
        sub = QLabel("PERSONAL AI ASSISTANT")
        sf = QFont("Segoe UI", 8)
        sf.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        sub.setFont(sf)
        sub.setStyleSheet("color:#7f8bc6;")
        box.addWidget(title)
        box.addWidget(sub)
        row.addLayout(box)
        row.addStretch(1)
        self.pill_online = QLabel("CHECKING")
        self.pill_online.setStyleSheet(pill_style("#8f9bd6"))
        self.pill_ai = QLabel(f"GEMINI  -  {config.MODEL}")
        self.pill_ai.setStyleSheet(pill_style("#c77dff"))
        row.addWidget(self.pill_online)
        row.addWidget(self.pill_ai)
        row.addSpacing(10)
        self.btn_min = IconButton("min", "Minimise", ("#4cc9f0", "#4361ee"), size=38)
        self.btn_max = IconButton("max", "Maximise", ("#4cc9f0", "#4361ee"), size=38)
        self.btn_close = IconButton("close", "Close window", ("#ff7a90", "#e5383b"), size=38)
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_max.clicked.connect(self.toggle_max)
        self.btn_close.clicked.connect(self.close)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            row.addWidget(b)
        return self.top

    def _build_sidebar(self) -> QWidget:
        side = HudPanel(radius=22)
        side.setFixedWidth(212)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(16, 24, 16, 18)
        lay.setSpacing(8)
        lay.addWidget(section_label("MENU"))
        lay.addSpacing(6)
        self.nav = {}
        for name, kind, label in (("chat", "plus", "New Chat"), ("history", "clock", "History"),
                                  ("memory", "chip", "Memory"), ("settings", "gear", "Settings")):
            b = NavButton(kind, label)
            b.clicked.connect(self.new_chat if name == "chat" else (lambda _=False, n=name: self.show_page(n)))
            self.nav[name] = b
            lay.addWidget(b)
        lay.addStretch(1)
        self.mode_label = QLabel("KEYBOARD MODE")
        self.mode_label.setStyleSheet("color:#6f7bb3; font-size:10px; letter-spacing:2px;")
        lay.addWidget(self.mode_label, 0, Qt.AlignHCenter)
        ver = QLabel("MOMO  v4")
        ver.setStyleSheet("color:#46507f; font-size:10px; letter-spacing:2px;")
        lay.addWidget(ver, 0, Qt.AlignHCenter)
        return side

    def _build_centre(self) -> QWidget:
        box = QWidget()
        box.setFixedWidth(430)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addStretch(1)
        self.orb = Orb()
        lay.addWidget(self.orb, 0, Qt.AlignHCenter)
        self.status = QLabel(STATE_TEXT["idle"])
        self.status.setAlignment(Qt.AlignHCenter)
        self.status.setStyleSheet("color:#9be7ff; font-size:16px; font-weight:600; letter-spacing:2px;")
        lay.addWidget(self.status)
        lay.addSpacing(10)

        stats = HudPanel(radius=20)
        stats.setFixedHeight(172)
        sl = QVBoxLayout(stats)
        sl.setContentsMargins(18, 14, 18, 8)
        sl.addWidget(section_label("SYSTEM"))
        row = QHBoxLayout()
        self.g_cpu, self.g_ram, self.g_net = Gauge("CPU"), Gauge("RAM"), Gauge("INTERNET")
        for g in (self.g_cpu, self.g_ram, self.g_net):
            row.addWidget(g)
        sl.addLayout(row)
        lay.addWidget(stats)
        lay.addStretch(1)
        return box

    def _scroll_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.viewport().setAutoFillBackground(False)
        holder = QWidget()
        holder.setObjectName("holder")
        holder.setStyleSheet("#holder { background: transparent; }")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(10)
        layout.addStretch(1)
        scroll.setWidget(holder)
        return scroll, layout

    def _build_right(self) -> QWidget:
        panel = HudPanel(radius=24)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)
        head = QHBoxLayout()
        self.page_title = section_label("CONVERSATION")
        self.clock = QLabel("")
        self.clock.setStyleSheet("color:#6f7bb3; font-size:11px; letter-spacing:1px;")
        head.addWidget(self.page_title)
        head.addStretch(1)
        head.addWidget(self.clock)
        lay.addLayout(head)

        self.stack = None  # created below (pages), the input stack is self.input_stack
        self.pages = QStackedWidget()
        self.scroll, self.msg_layout = self._scroll_page()                 # 0 chat
        self.hist_scroll, self.hist_layout = self._scroll_page()           # 1 history
        self.mem_scroll, self.mem_layout = self._scroll_page()             # 2 memory
        for page in (self.scroll, self.hist_scroll, self.mem_scroll, self._build_settings()):
            self.pages.addWidget(page)
        lay.addWidget(self.pages, 1)

        self.chips_box = QWidget()
        chips = QHBoxLayout(self.chips_box)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(8)
        for label, command in (("Open YouTube", "open youtube"), ("To-do list", "show my todo list"),
                               ("Screenshot", "take a screenshot"), ("Daily report", "daily report")):
            chip = QPushButton(label)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setFixedHeight(30)
            chip.setStyleSheet(PILL_STYLE)
            chip.clicked.connect(lambda _=False, c=command: self.quick(c))
            chips.addWidget(chip)
        chips.addStretch(1)
        lay.addWidget(self.chips_box)

        bar = Glass(radius=31, alpha=0.08)
        bar.setFixedHeight(64)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 0, 9, 0)
        bl.setSpacing(10)
        self.btn_keyboard = IconButton("keyboard", "Keyboard", ("#4cc9f0", "#4361ee"), size=46)
        self.btn_voice = IconButton("mic", "Voice", ("#80ffdb", "#14b8a6"), size=46)
        self.btn_keyboard.set_active(True)
        self.input_stack = QStackedWidget()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Type your command...")
        self.entry.returnPressed.connect(self.send)
        self.bars = VoiceBars()
        self.input_stack.addWidget(self.entry)
        self.input_stack.addWidget(self.bars)
        self.btn_send = IconButton("send", "Send", ("#4cc9f0", "#4361ee"), size=46)
        self.btn_send.set_active(True)
        self.btn_send.clicked.connect(self.send)
        self.btn_keyboard.clicked.connect(lambda: self.set_mode("keyboard"))
        self.btn_voice.clicked.connect(lambda: self.set_mode("voice"))
        bl.addWidget(self.btn_keyboard)
        bl.addWidget(self.input_stack, 1)
        bl.addWidget(self.btn_voice)
        bl.addWidget(self.btn_send)
        lay.addWidget(bar)
        return panel

    def _build_settings(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 6, 0)
        lay.setSpacing(14)

        def row(title, text, widget):
            card = QFrame()
            card.setStyleSheet("QFrame { background: rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.08);"
                               "border-radius:14px; } QLabel { border:none; background:transparent; }")
            hl = QHBoxLayout(card)
            hl.setContentsMargins(18, 12, 18, 12)
            col = QVBoxLayout()
            col.setSpacing(2)
            t = QLabel(title)
            t.setStyleSheet("font-size:14px; font-weight:600; color:#ffffff;")
            d = QLabel(text)
            d.setStyleSheet("font-size:12px; color:#8f9bd6;")
            col.addWidget(t)
            col.addWidget(d)
            hl.addLayout(col, 1)
            hl.addWidget(widget)
            return card

        self.sw_voice = Switch(config.VOICE_REPLY)
        self.sw_voice.toggled.connect(lambda on: settings.update("voice_reply", on))
        self.sw_confirm = Switch(config.CONFIRM_BEFORE_SEND)
        self.sw_confirm.toggled.connect(lambda on: settings.update("confirm", on))
        lay.addWidget(row("Voice replies", "Momo speaks every reply", self.sw_voice))
        lay.addWidget(row("Confirm before sending", "Ask Yes / No before every message and call", self.sw_confirm))

        seg = QWidget()
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)
        self.engine_group = QButtonGroup(self)
        for label, value in (("Auto", "auto"), ("Neural", "edge"), ("Windows", "sapi"), ("Google", "gtts")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(config.VOICE_ENGINE == value)
            b.setStyleSheet(SEGMENT_STYLE)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=value: settings.update("voice_engine", v))
            self.engine_group.addButton(b)
            sl.addWidget(b)
        lay.addWidget(row("Voice engine", "Auto tries the best voice first", seg))

        tools = QWidget()
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(8)
        for label, fn in (("Test voice", self.test_voice), ("Open logs folder", self.open_logs)):
            b = QPushButton(label)
            b.setStyleSheet(PILL_STYLE)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(32)
            b.clicked.connect(lambda _=False, f=fn: f())
            tl.addWidget(b)
        lay.addWidget(row("Tools", "Check the voice or open the error log", tools))

        info = QLabel(f"AI engine: Gemini ({config.MODEL})\nWorkspace: {config.WORKSPACE}")
        info.setStyleSheet("color:#6f7bb3; font-size:12px; padding:4px 6px;")
        lay.addWidget(info)
        lay.addStretch(1)
        return page

    # ---------------- pages ----------------
    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def show_page(self, name: str):
        self.page = name
        self.pages.setCurrentIndex(self.PAGES[name])
        self.page_title.setText(self.TITLES[name])
        for key, btn in self.nav.items():
            btn.set_active(key == name)
        self.chips_box.setVisible(name == "chat")
        if name == "history":
            self.refresh_history()
        elif name == "memory":
            self.refresh_memory()

    def refresh_history(self):
        self._clear_layout(self.hist_layout)
        items = store.load("history", [])[-40:][::-1]
        if not items:
            note = QLabel("No commands yet.")
            note.setStyleSheet("color:#8f9bd6; font-size:13px;")
            self.hist_layout.addWidget(note)
        for it in items:
            b = QPushButton(it.get("text", ""))
            b.setToolTip(it.get("ts", ""))
            b.setStyleSheet(ROW_STYLE)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, t=it.get("text", ""): self.quick(t))
            self.hist_layout.addWidget(b)
        if items:
            clear = QPushButton("Clear history")
            clear.setStyleSheet(PILL_STYLE)
            clear.setFixedHeight(30)
            clear.clicked.connect(lambda: (store.save("history", []), self.refresh_history()))
            self.hist_layout.addWidget(clear, 0, Qt.AlignLeft)
        self.hist_layout.addStretch(1)

    def refresh_memory(self):
        self._clear_layout(self.mem_layout)
        facts, projects = store.load("facts", []), store.load("projects", {})
        if not facts and not projects:
            note = QLabel("Nothing remembered yet. Say: remember that my exam is on Friday.")
            note.setWordWrap(True)
            note.setStyleSheet("color:#8f9bd6; font-size:13px;")
            self.mem_layout.addWidget(note)
        if facts:
            self.mem_layout.addWidget(section_label("THINGS I REMEMBER"))
        for f in facts[::-1]:
            card = QLabel(f.get("text", ""))
            card.setWordWrap(True)
            card.setStyleSheet("background: rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.09);"
                               "border-radius:12px; padding:10px 14px; font-size:13px;")
            self.mem_layout.addWidget(card)
        if projects:
            self.mem_layout.addWidget(section_label("PROJECTS"))
        for name, notes in projects.items():
            text = name + ":  " + "; ".join(n.get("note", "") for n in notes[-3:])
            card = QLabel(text)
            card.setWordWrap(True)
            card.setStyleSheet("background: rgba(199,125,255,0.10); border:1px solid rgba(199,125,255,0.30);"
                               "border-radius:12px; padding:10px 14px; font-size:13px;")
            self.mem_layout.addWidget(card)
        self.mem_layout.addStretch(1)

    def new_chat(self):
        self._close_confirm()
        self._clear_layout(self.msg_layout)
        self.msg_layout.addStretch(1)
        if self.assistant:
            self.assistant.post("newchat")
        self.show_page("chat")
        self.add_message("status", GREETING)

    # ---------------- settings actions ----------------
    def test_voice(self):
        from .speech import speaker
        if not config.VOICE_REPLY:
            self.add_message("status", "Voice replies are turned off. Switch them on first.")
        else:
            self.add_message("status", "Testing the voice...")
            speaker.say("Sir, this is a voice test.", "en")

    def open_logs(self):
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(log_dir()))
            else:
                subprocess.Popen(["xdg-open", str(log_dir())])
        except Exception as e:
            self.add_message("error", f"I could not open the logs folder: {short_reason(e)}")

    # ---------------- timers ----------------
    def _frame(self):
        if self.mode == "voice":
            self.btn_voice.update()
        self.update()

    def _slow_tick(self):
        self.clock.setText(datetime.now().strftime("%a %d %b  -  %I:%M %p"))
        try:
            import psutil
            cpu, ram = psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
            self.g_cpu.set_value(cpu / 100, f"{cpu:.0f}%", level_color(cpu))
            self.g_ram.set_value(ram / 100, f"{ram:.0f}%", level_color(ram))
        except Exception:
            self.g_cpu.set_value(0, "--")
            self.g_ram.set_value(0, "--")
        ok, ms = self.net.ok, self.net.ms
        if ok is None:
            self.g_net.set_value(0, "..", "#8f9bd6")
        elif ok:
            self.g_net.set_value(max(0.15, 1 - ms / 500), f"{ms}ms", "#34d399")
            self.pill_online.setText("ONLINE")
            self.pill_online.setStyleSheet(pill_style("#34d399"))
        else:
            self.g_net.set_value(0, "OFF", "#ff5d73")
            self.pill_online.setText("OFFLINE")
            self.pill_online.setStyleSheet(pill_style("#ff5d73"))

    # ---------------- messages ----------------
    def _scroll_down(self):
        QTimer.singleShot(40, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))

    def _close_confirm(self):
        """Disable the Yes / No buttons of the open question (it has been answered)."""
        if self.confirm_row is not None:
            for b in getattr(self.confirm_row, "buttons", []):
                b.setEnabled(False)
            self.confirm_row = None

    def add_message(self, kind, text):
        if kind == "confirm":
            self._close_confirm()
            row = make_bubble("confirm", text, buttons=[("Yes", lambda: self.answer("yes"), YES_STYLE),
                                                        ("No", lambda: self.answer("no"), NO_STYLE)])
            self.confirm_row = row
        else:
            row = make_bubble(kind, text)
        self.msg_layout.addWidget(row)
        fade_in(row)
        self._scroll_down()

    def answer(self, text):
        """Yes / No button of a confirmation question."""
        self._close_confirm()
        self.msg_layout.addWidget(make_bubble("user", text.capitalize()))
        self._scroll_down()
        if self.assistant:
            self.assistant.post("text", text)

    def quick(self, command):
        """A quick-command chip or a history item."""
        self.entry.setText(command)
        self.send()

    # ---------------- state ----------------
    def set_state(self, state):
        self.orb.state = state if state in STATE_COLORS else "idle"
        self.status.setText(STATE_TEXT["speaking" if self.orb.speaking else self.orb.state].upper())
        self.bars.active = state == "listening"

    def set_speaking(self, value):
        self.orb.speaking = value
        self.status.setText(STATE_TEXT["speaking" if value else self.orb.state].upper())

    def apply_mode(self, mode):
        """Highlight the selected mode button and swap the input area."""
        self.mode = mode
        self.btn_keyboard.set_active(mode == "keyboard")
        self.btn_voice.set_active(mode == "voice")
        self.mode_label.setText("VOICE MODE" if mode == "voice" else "KEYBOARD MODE")
        self.input_stack.setCurrentIndex(1 if mode == "voice" else 0)
        self.btn_send.setVisible(mode == "keyboard")
        if mode == "keyboard":
            self.entry.setFocus()
        else:
            self.bars.active = True

    def set_mode(self, mode):
        if mode == self.mode:
            return  # the sentence is said only once, when the mode is selected
        self.apply_mode(mode)
        if self.assistant:
            self.assistant.post("mode", mode)

    def send(self):
        text = self.entry.text().strip()
        if not text:
            return
        self.entry.clear()
        if self.page != "chat":
            self.show_page("chat")
        self._close_confirm()
        row = make_bubble("user", text)
        self.msg_layout.addWidget(row)
        fade_in(row)
        self._scroll_down()
        if self.assistant:
            self.assistant.post("text", text)

    # ---------------- window behaviour ----------------
    def toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def closeEvent(self, event):
        if self.assistant:
            self.assistant.detached = True
            self.assistant.post("quit")
        event.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.isMaximized():
            self.drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self.drag)

    def mouseReleaseEvent(self, e):
        self.drag = None

    def mouseDoubleClickEvent(self, e):
        if e.position().y() < self.top.geometry().bottom() + 18:
            self.toggle_max()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        t = time.time() - self.t0
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 30, 30)
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0.0, QColor("#050a1a"))
        g.setColorAt(0.55, QColor("#0a1030"))
        g.setColorAt(1.0, QColor("#06142a"))
        p.fillPath(path, g)
        p.setClipPath(path)
        p.setPen(QPen(rgba("#4cc9f0", 9), 1))  # faint HUD grid
        for x in range(0, w, 46):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, 46):
            p.drawLine(0, y, w, y)
        centre = self.orb.mapTo(self, self.orb.rect().center())
        glow = QRadialGradient(QPointF(centre), 460)
        glow.setColorAt(0.0, rgba(self.orb.c1, 30))
        glow.setColorAt(1.0, rgba(self.orb.c2, 0))
        p.fillRect(self.rect(), glow)
        scan_y = (t * 70) % (h + 120) - 60  # slow scanning band
        band = QLinearGradient(0, scan_y - 50, 0, scan_y + 50)
        band.setColorAt(0.0, rgba("#4cc9f0", 0))
        band.setColorAt(0.5, rgba("#4cc9f0", 16))
        band.setColorAt(1.0, rgba("#4cc9f0", 0))
        p.fillRect(QRectF(0, scan_y - 50, w, 100), band)
        self.particles.paint(p, w, h, t, self.orb.c1)
        line_y = self.top.geometry().bottom() + 8
        p.setPen(QPen(rgba("#4cc9f0", 50), 1))
        p.drawLine(24, line_y, w - 24, line_y)
        p.setClipping(False)
        p.setPen(QPen(rgba("#4cc9f0", 60), 1.2))
        p.drawPath(path)


def main():
    console.enable_ansi()
    settings.load()  # settings changed in the window earlier
    app = QApplication(sys.argv)
    font = QFont("Segoe UI", 10)
    font.setFamilies(["Segoe UI", "Nirmala UI", "Noto Sans", "Arial"])  # Nirmala shows Bengali text
    app.setFont(font)
    bridge = Bridge()
    assistant = Assistant(bridge)
    window = MomoWindow(bridge, assistant)
    window.show()
    assistant.start()
    app.exec()
    # Window closed: the program stops, but a task that is already running is finished first.
    assistant.detached = True
    assistant.post("quit")
    if assistant.busy:
        console.show("Window closed. Finishing the running task...", "status")
    assistant.join(timeout=600)
    os._exit(0)  # leaves no helper threads behind; Edge and opened apps are untouched


if __name__ == "__main__":
    main()