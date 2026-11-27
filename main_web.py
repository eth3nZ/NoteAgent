"""Native macOS WebView entry point."""
import os
import sys
import time
sys.dont_write_bytecode = True
os.environ.setdefault('NOTEAGENT_PROCESS_STARTED',str(time.time()))
from app.desktop.host import run

def main():
    run()

if __name__ == '__main__':
    main()
