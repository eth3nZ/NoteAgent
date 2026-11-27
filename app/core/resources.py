"""Resolve bundled resources in source checkouts, wheels and py2app bundles."""
import os
import sys
from pathlib import Path


def resource_path(relative):
    if getattr(sys, 'frozen', False):
        root = Path(os.environ.get('RESOURCEPATH', str(Path(sys.executable).parent.parent/'Resources')))
        if (root/relative).exists():
            return root/relative
        # py2app keeps explicitly included packages unpacked under lib/pythonX.Y.
        return Path(__file__).resolve().parents[2]/relative
    return Path(__file__).resolve().parents[2]/relative
