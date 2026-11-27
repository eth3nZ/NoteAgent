"""Build a fresh local macOS bundle with the current interpreter; no credentials read."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def sign_bundle(bundle, output):
    # --deep does not reliably re-sign Mach-O resources under Python packages
    # (notably Pillow's .dylibs). Sign every real binary before the outer bundle.
    magic = {b'\xcf\xfa\xed\xfe',b'\xce\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',
             b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca',b'\xca\xfe\xba\xbf'}
    def sign(path, deep=False):
        command = ['codesign','--force'] + (['--deep'] if deep else []) + ['--sign','-',str(path)]
        for attempt in range(3):
            result = subprocess.run(command,stdout=output,stderr=subprocess.STDOUT)
            if result.returncode == 0:
                return
            if attempt < 2:
                time.sleep(1)
        raise subprocess.CalledProcessError(result.returncode,command)
    for path in bundle.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open('rb') as file:
            binary = file.read(4) in magic
        if binary:
            sign(path)
    sign(bundle,deep=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qt',action='store_true',help='Build the legacy comparison app instead')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('macOS is required.')
    root = Path(__file__).resolve().parents[1]
    bundle = root/('dev_artifacts/packaging-qt' if args.qt else 'dist')/'NoteAgent.app'
    intermediate = root/'build'/('qt' if args.qt else 'webview')
    # Incremental replacement can retain stale signatures or Python-distribution
    # libraries. Delete only these known generated build locations.
    for path in (bundle,intermediate):
        if path.exists():
            shutil.rmtree(path)
    log = root/'dev_artifacts'/('build-qt.log' if args.qt else 'build-native.log')
    log.parent.mkdir(exist_ok=True)
    env = dict(os.environ,NOTEAGENT_BUILD_UI='qt' if args.qt else 'webview')
    with log.open('w') as output:
        result = subprocess.run([sys.executable,'setup_py2app.py','py2app'],cwd=root,env=env,
                                stdout=output,stderr=subprocess.STDOUT)
    # Some macOS versions reject py2app's batched signing. Retry the standard
    # recursive ad-hoc signature only after that specific final-stage failure.
    signing_failure = "RuntimeError: Cannot sign bundle" in log.read_text(errors='replace')[-2000:]
    if result.returncode and not signing_failure:
        print(f'Build failed. See {log}')
        return result.returncode
    with log.open('a') as output:
        sign_bundle(bundle,output)
    subprocess.run(['codesign','--verify','--deep','--strict',str(bundle)],check=True)
    size = sum(path.stat().st_size for path in bundle.rglob('*') if path.is_file() and not path.is_symlink())
    print(f'Built {bundle}\nFile size: {size/1024**2:.1f} MiB. Local ad-hoc signature; not notarized.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
