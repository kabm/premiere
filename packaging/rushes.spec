# PyInstaller spec: one folder ("Rushes") holding two programs that share the same runtime:
#   rushes.exe         command-line tool (console)
#   Rushes Studio.exe  desktop app (no console window; opens the editor in the browser)
# Build:  pyinstaller --noconfirm --clean packaging/rushes.spec
# FFmpeg is not bundled by this spec; the CI copies it to dist/Rushes/ffmpeg/bin afterwards.
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(SPECPATH, "windows", "rushes.ico")
ICON = ICON if os.path.exists(ICON) else None

datas = []
static = os.path.join(ROOT, "rushes", "studio", "static")
if os.path.isdir(static):
    datas.append((static, os.path.join("rushes", "studio", "static")))
hidden = collect_submodules("rushes")
common = dict(pathex=[ROOT], hiddenimports=hidden, datas=datas,
              excludes=["tkinter", "unittest", "pydoc_data", "test"])

cli = Analysis([os.path.join(SPECPATH, "entry_cli.py")], **common)
studio = Analysis([os.path.join(SPECPATH, "entry_studio.py")], **common)

cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="rushes",
              console=True, icon=ICON, upx=False)
studio_exe = EXE(PYZ(studio.pure), studio.scripts, [], exclude_binaries=True, name="Rushes Studio",
                 console=False, icon=ICON, upx=False)

COLLECT(cli_exe, cli.binaries, cli.datas, studio_exe, studio.binaries, studio.datas,
        name="Rushes", upx=False)
