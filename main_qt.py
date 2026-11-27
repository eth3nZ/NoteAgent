"""
NoteAgent - Intelligent Note Generation Tool
Main entry point for the application.
"""

import sys
import os
import time
sys.dont_write_bytecode = True
_started = time.time()

# Ensure the app module is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from app.ui.branding import application_icon
from app.ui.main_window import MainWindow


def main():
    """Application entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("NoteAgent")
    app.setOrganizationName("NoteAgent")
    app.setWindowIcon(application_icon())

    # Set default font
    font = QFont()
    font.setPointSize(13)
    app.setFont(font)

    # Create and show main window
    MainWindow.CONFIG_FILE = os.environ.get('NOTEAGENT_CONFIG', MainWindow.CONFIG_FILE)
    window = MainWindow()
    window.show()

    if os.environ.get('NOTEAGENT_DIAGNOSTICS'):
        from PyQt6.QtCore import QTimer
        from pathlib import Path
        import json
        def report_startup():
            Path(os.environ['NOTEAGENT_DIAGNOSTICS']).write_text(json.dumps({
                'ui':'qt','first_event_seconds':round(time.time()-_started,3),
                'frozen':bool(getattr(sys,'frozen',False))}))
        QTimer.singleShot(0,report_startup)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
