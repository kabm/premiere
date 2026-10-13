"""Process-level setup for installed builds (bundled FFmpeg, Windows console handling)."""

import os
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
_done = False


def app_dir():
    """Folder of the installed application (PyInstaller) or the source checkout."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_data_dir():
    """Per-user folder for settings, logs and caches (created on demand)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "Rushes")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        path = os.path.join(base, "rushes")
    os.makedirs(path, exist_ok=True)
    return path


def bundled_ffmpeg_dirs():
    roots = [app_dir()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.insert(0, meipass)
    dirs = []
    for root in roots:
        for sub in (os.path.join("ffmpeg", "bin"), "ffmpeg", ""):
            d = os.path.join(root, sub)
            exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
            if os.path.isfile(os.path.join(d, exe)) and d not in dirs:
                dirs.append(d)
    return dirs


def _hide_child_consoles():
    """A windowed (no console) app would flash a console window for every ffmpeg call."""
    original = subprocess.Popen.__init__

    def patched(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched


def setup(windowed=False):
    """Call once at program start. Safe to call more than once."""
    global _done
    if _done:
        return
    _done = True
    override = os.environ.get("RUSHES_FFMPEG_DIR")
    dirs = [override] if override else bundled_ffmpeg_dirs()
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
    if os.name == "nt" and windowed:
        _hide_child_consoles()
    if sys.stdout is None or sys.stderr is None:
        # Windowed PyInstaller builds have no console streams; keep a log instead.
        log = open(os.path.join(user_data_dir(), "rushes.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    # Make stdout/stderr UTF-8 so file names with accents don't crash on Windows consoles.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
