"""Build a macOS .app. NOTEAGENT_BUILD_UI=qt builds the comparison baseline only."""
import os
import sys
from pathlib import Path
from setuptools import setup, Distribution


class BundleDistribution(Distribution):
    def parse_config_files(self, filenames=None, ignore_option_errors=False):
        super().parse_config_files(filenames, ignore_option_errors=ignore_option_errors)
        # Dependencies have already been installed in the build environment. py2app
        # must collect imports, never run setuptools' retired dependency installer.
        self.install_requires = []
        self.extras_require = {}

root = Path(__file__).resolve().parent
legacy = os.environ.get('NOTEAGENT_BUILD_UI') == 'qt'
# py2app does not discover all of Conda's @rpath stdlib dependencies. Include
# the real libraries when present, rather than accidentally loading macOS's
# older SQLite/OpenSSL or requiring the developer's Python installation.
native_libraries = [Path(sys.base_prefix)/'lib'/name for name in (
    'libffi.8.dylib','libsqlite3.0.dylib','libssl.3.dylib','libcrypto.3.dylib',
    'liblzma.5.dylib','libbz2.1.0.dylib','libz.1.dylib')]
data = []
for folder in ['prompts']:
    for path in (root/folder).rglob('*'):
        if path.is_file() and '__pycache__' not in str(path) and path.suffix not in {'.pyc','.map'}:
            data.append((str(path.parent.relative_to(root)),[str(path)]))
setup(
    distclass=BundleDistribution,
    name='NoteAgent', version='0.3.0',
    app=[{'script':'main_qt.py' if legacy else 'main_web.py','dest_base':'NoteAgent-Qt' if legacy else 'NoteAgent'}],
    data_files=data,
    options={'py2app':{
        'argv_emulation':False,'iconfile':'app/assets/noteagent.icns',
        'strip':False,
        'frameworks':[str(path) for path in native_libraries if path.is_file()],
        'packages':['app','pymupdf','pptx','nbformat','PIL','httpx','jsonschema'],
        'excludes':(['webview','PyObjCTools'] if legacy else ['PyQt6','PyQt5','PySide6','PySide2','app.ui']) + ['tkinter','pytest','IPython','matplotlib','numpy','scipy'],
        'dist_dir':'dev_artifacts/packaging-qt' if legacy else 'dist',
        'bdist_base':'build/qt' if legacy else 'build/webview',
        'plist':{'CFBundleName':'NoteAgent','CFBundleDisplayName':'NoteAgent',
                 'CFBundleIdentifier':'org.noteagent.desktop.qt-baseline' if legacy else 'org.noteagent.desktop',
                 'NSHighResolutionCapable':True,'NSRequiresAquaSystemAppearance':True,
                 'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True},
                 'NSHumanReadableCopyright':'NoteAgent contributors'},
    }},
)
