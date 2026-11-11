"""One short-lived background thread per operation; UI updates only use signals."""

from PyQt6.QtCore import QThread, pyqtSignal


class TaskWorker(QThread):
    progress = pyqtSignal(str)
    stream = pyqtSignal(str)
    state = pyqtSignal(object)
    result = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.result.emit(self.operation(self))
        except Exception as exc:
            self.failed.emit(str(exc))
