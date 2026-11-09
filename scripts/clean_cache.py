"""Inspect the application cache; --apply removes only unused cached content."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QLockFile

from app.core.maintenance import clean_storage, storage_info
from app.core.task_store import TaskBusy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path.home()/'.noteagent_config.json',
                        help='Profile path; credentials are not read')
    parser.add_argument('--apply', action='store_true', help='Clean expired and duplicate cache; exported files stay intact')
    args = parser.parse_args()
    path = args.config.expanduser().parent/'.noteagent'/'tasks.sqlite3'
    if not args.apply:
        print(json.dumps(storage_info(path), indent=2))
        return 0
    lock = QLockFile(str(args.config.expanduser())+'.lock')
    if not lock.tryLock(0):
        print('This profile is open in NoteAgent. Use Settings → Storage there, or close it before using this command.')
        return 2
    try:
        print(json.dumps(clean_storage(path), indent=2))
    except TaskBusy as exc:
        print(str(exc))
        return 2
    finally:
        lock.unlock()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
