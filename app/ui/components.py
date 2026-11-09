"""Small Qt widgets for estimates and activity; no network or task logic."""

from datetime import datetime

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)


class BusyIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.active = False
        self.angle = 0
        self.color = QColor("#93968F")
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._advance)
        self.setAccessibleName("Idle")

    def set_state(self, active=False, color="#93968F"):
        self.active, self.color = active, QColor(color)
        self.setAccessibleName("Working" if active else "Idle")
        if active and self.isVisible():
            self.timer.start()
        else:
            self.timer.stop()
        self.update()

    def _advance(self):
        self.angle = (self.angle + 18) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.active:
            pen = QPen(self.color, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(QRectF(3, 3, 10, 10), -self.angle * 16, 255 * 16)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.color)
            painter.drawEllipse(QRectF(5, 5, 6, 6))

    def showEvent(self, event):
        if self.active:
            self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)


class MetricCard(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setMinimumHeight(104)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        self.title = QLabel(title)
        self.title.setObjectName("metricTitle")
        self.value = QLabel("—")
        self.value.setObjectName("metricValue")
        self.detail = QLabel()
        self.detail.setObjectName("metricDetail")
        self.detail.setWordWrap(True)
        for label in (self.title, self.value, self.detail):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setMinimumWidth(0)
            layout.addWidget(label)
        layout.addStretch()

    def set_content(self, value, detail, tooltip=""):
        self.value.setText(value)
        self.detail.setText(detail)
        self.setToolTip(tooltip)
        self.setAccessibleName(f"{self.title.text()}: {value}. {detail}")


class ActivityPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.toggle_button = QPushButton("▸  Activity")
        self.toggle_button.setObjectName("activityToggle")
        self.toggle_button.setCheckable(True)
        self.toggle_button.toggled.connect(self.set_expanded)
        self.indicator = BusyIndicator(self)
        self.latest = QLabel("No activity yet")
        self.latest.setObjectName("muted")
        self.latest.setMinimumWidth(0)
        self.latest.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.latest.setTextFormat(Qt.TextFormat.PlainText)
        self.count = QLabel("0 events")
        self.count.setObjectName("activityCount")
        row.addWidget(self.toggle_button)
        row.addWidget(self.indicator)
        row.addWidget(self.latest, 1)
        row.addWidget(self.count)
        layout.addLayout(row)
        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 5, 0, 6)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("activityLog")
        self.log.document().setMaximumBlockCount(500)
        self.log.setFixedHeight(116)
        body_layout.addWidget(self.log)
        self.body.setMaximumHeight(0)
        layout.addWidget(self.body)
        self.animation = QPropertyAnimation(self.body, b"maximumHeight", self)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.event_count = 0
        self._last_message = None

    def set_expanded(self, expanded):
        self.toggle_button.setText("▾  Activity" if expanded else "▸  Activity")
        self.toggle_button.setAccessibleName("Hide activity" if expanded else "Show activity")
        self.animation.stop()
        self.animation.setStartValue(self.body.maximumHeight())
        self.animation.setEndValue(127 if expanded else 0)
        self.animation.start()

    def add_event(self, message):
        if message == self._last_message:
            return
        self._last_message = message
        self.event_count += 1
        self.latest.setText(message.replace("\n", " · "))
        self.latest.setToolTip(message)
        self.count.setText(f"{self.event_count} event" + ("s" if self.event_count != 1 else ""))
        scrollbar = self.log.verticalScrollBar()
        position = scrollbar.value()
        follow = position >= scrollbar.maximum() - 4
        self.log.appendPlainText(f"{datetime.now():%H:%M:%S}  {message}")
        if not follow:
            scrollbar.setValue(position)

    def clear(self):
        self.log.clear()
        self.event_count = 0
        self._last_message = None
        self.latest.setText("No activity yet")
        self.count.setText("0 events")

    def set_running(self, running, stopping=False):
        self.indicator.set_state(running, "#B0783D" if stopping else "#49685A")

    def stop_animation(self):
        self.indicator.set_state(False)
        self.animation.stop()
