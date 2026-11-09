"""Export a clean rounded tile from the original logo, using Qt's native geometry.

Run with .venv/bin/python scripts/export_icons.py. No image API or network is used.
"""

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PIL import Image
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter


def export_icons():
    assets = Path(__file__).resolve().parents[1] / "app" / "assets"
    source = QImage(str(assets / "noteagent.png"))
    if source.isNull():
        raise ValueError("The original NoteAgent logo is missing.")
    size, padding, radius = 1024, 64, 196
    bounds = QRectF(padding, padding, size - 2 * padding, size - 2 * padding)
    rounded = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(bounds, source)
    painter.end()

    mask = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    mask.fill(Qt.GlobalColor.transparent)
    painter = QPainter(mask)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(Qt.GlobalColor.white)
    painter.drawRoundedRect(bounds, radius, radius)
    painter.end()

    painter = QPainter(rounded)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, mask)
    painter.end()
    output = assets / "noteagent-rounded.png"
    if not rounded.save(str(output)):
        raise OSError("Could not write the rounded application icon.")
    with Image.open(output) as image:
        image.save(output, optimize=True)
        image.save(assets / "noteagent.icns", format="ICNS")
        image.save(assets / "noteagent.ico", format="ICO",
                   sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    application = QGuiApplication([])
    export_icons()
