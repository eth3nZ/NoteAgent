"""Shared application artwork, bundled with the Python package."""

from pathlib import Path

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap


ASSETS = Path(__file__).resolve().parents[1] / "assets"


def application_icon() -> QIcon:
    return QIcon(str(ASSETS / "noteagent-rounded.png"))


def menu_bar_icon() -> QIcon:
    """A native template icon, tinted by macOS for light/dark menu bars."""
    source = QIcon(str(ASSETS / "noteagent-menubar.svg"))
    # Cocoa otherwise expands an SVG to the full menu-bar height, which is
    # taller on notched displays. Supply explicit normal/Retina representations.
    icon = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(QSize(24 * scale, 16 * scale))
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.setDevicePixelRatio(scale)
        painter = QPainter(pixmap)
        source.paint(painter, QRect(0, 0, 24, 16))
        painter.end()
        icon.addPixmap(pixmap)
    icon.setIsMask(True)
    return icon
