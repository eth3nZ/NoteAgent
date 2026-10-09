"""
NoteAgent - Intelligent Note Generation Tool
Main entry point for the application.
"""

import sys
import os

# Ensure the app module is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from app.ui.main_window import MainWindow


def main():
    """Application entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("NoteAgent")
    app.setOrganizationName("NoteAgent")

    # Set default font
    font = QFont()
    font.setPointSize(13)
    app.setFont(font)

    # Create and show main window
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()