"""Rushes Studio: a local editing UI served to the browser from this machine only.

The server listens on 127.0.0.1, requires a per-launch secret token on every API call and checks
the Host header, so other websites and other machines cannot drive it.
"""

import argparse
import json
import mimetypes
import os
import secrets
import shutil
import string
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import __version__, runtime
from ..lut import LUT_EXTS, Lut
from ..presets import PRESETS
from ..probe import IMAGE_EXTS, probe
from ..project import Project
from ..util import RushesError
from .jobs import JobManager
from .preview import program_frame

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MEDIA_EXTS = {".mov", ".mp4", ".mxf", ".mkv", ".avi", ".m4v", ".mts", ".m2ts", ".mpg", ".webm",
              ".wav", ".aif", ".aiff", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus"} | IMAGE_EXTS
PROJECT_EXTS = {".json"}
SUBTITLE_EXTS = {".srt", ".vtt"}
IDLE_TIMEOUT = 90  # seconds without a browser heartbeat (and no render running) before exiting


def file_kind(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in LUT_EXTS:
        return "lut"
    if ext in PROJECT_EXTS:
        return "project"
    if ext in SUBTITLE_EXTS:
        return "subtitle"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in {".wav", ".aif", ".aiff", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus"}:
        return "audio"
    if ext in MEDIA_EXTS:
        return "video"
    return None


def windows_drives():
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]


class Settings:
    def __init__(self):
        self.path = os.path.join(runtime.user_data_dir(), "settings.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except (OSError, ValueError):
            self.data = {}

    def add_recent(self, path):
        recent = [p for p in self.data.get("recent", []) if p != path]
        self.data["recent"] = [path] + recent[:9]
        self.save()

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2)
        except OSError:
            pass


class Studio:
    def __init__(self):
        self.token = secrets.token_urlsafe(18)
        self.jobs = JobManager()
        self.settings = Settings()
        self.last_seen = time.time()
        self.port = None
        self.server = None
        self.cache_dir = os.path.join(runtime.user_data_dir(), "cache")
        os.makedirs(self.cache_dir, exist_ok=True)

    # -- API ----------------------------------------------------------------

    def api_info(self, _):
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True).stdout
            ffmpeg = out.splitlines()[0] if out else None
        except OSError:
            ffmpeg = None
        transforms = []
        try:
            from .. import colormgmt
            if hasattr(colormgmt, "list_transforms"):
                transforms = colormgmt.list_transforms()
        except Exception:
            transforms = []
        return {
            "version": __version__,
            "ffmpeg": ffmpeg,
            "platform": sys.platform,
            "home": os.path.expanduser("~"),
            "recent": [p for p in self.settings.data.get("recent", []) if os.path.exists(p)],
            "presets": [{"name": n, "category": p["category"], "description": p["description"], "ext": p["ext"],
                         "sequence": p["sequence"], "audio_only": p["audio_only"]} for n, p in PRESETS.items()],
            "transforms": transforms,
        }

    def api_fs(self, q):
        path = q.get("path") or self.settings.data.get("last_dir") or os.path.expanduser("~")
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            path = os.path.dirname(path) if os.path.isdir(os.path.dirname(path)) else os.path.expanduser("~")
        entries = []
        try:
            names = sorted(os.listdir(path), key=lambda n: n.lower())
        except OSError as exc:
            raise RushesError(f"Cannot open folder: {exc}") from None
        for name in names:
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            if os.path.isdir(full):
                entries.append({"name": name, "path": full, "dir": True})
            else:
                kind = file_kind(name)
                if kind:
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        size = None
                    entries.append({"name": name, "path": full, "dir": False, "kind": kind, "size": size})
        self.settings.data["last_dir"] = path
        parent = os.path.dirname(path)
        return {"path": path, "parent": parent if parent != path else None, "entries": entries,
                "drives": windows_drives() if os.name == "nt" else ["/"], "sep": os.sep}

    def api_probe(self, body):
        info = probe(body["path"])
        v = info.video or {}
        return {"path": info.path, "duration": info.duration, "width": info.width, "height": info.height,
                "fps": float(info.fps) if info.fps else None, "has_audio": info.has_audio,
                "has_video": info.video is not None, "is_image": info.is_image,
                "codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"), "summary": info.summary()}

    def _project(self, body):
        data = body.get("project") or {}
        base = body.get("base_dir") or os.path.expanduser("~")
        return data, base

    def api_validate(self, body):
        data, base = self._project(body)
        project = Project(data, base)
        return {"duration": project.duration, "fps": float(project.fps),
                "clips": [{"timeline_start": c["timeline_start"], "duration": c["duration"],
                           "src_len": c["src_len"]} for c in project.clips]}

    def api_frame(self, body):
        data, base = self._project(body)
        return program_frame(data, base, float(body.get("time", 0)), compare=bool(body.get("compare")),
                             scopes=bool(body.get("scopes")), width=int(body.get("width", 960)),
                             cache_dir=self.cache_dir)

    def api_render(self, body):
        data, base = self._project(body)
        draft = bool(body.get("draft"))
        output = body.get("output")
        if draft and not output:
            output = os.path.join(self.cache_dir, "preview.mp4")
        if not output:
            raise RushesError("Choose an output file")
        job = self.jobs.submit(data, base, body.get("preset"), output, draft=draft, label=body.get("label"))
        return job.to_dict()

    def api_jobs(self, _):
        return {"jobs": self.jobs.list()}

    def api_cancel(self, body):
        self.jobs.cancel(int(body["id"]))
        return {"ok": True}

    def api_clear(self, _):
        self.jobs.clear_finished()
        return {"ok": True}

    def api_open(self, body):
        path = os.path.abspath(body["path"])
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise RushesError(f"Cannot open project: {exc}") from None
        self.settings.add_recent(path)
        return {"project": data, "path": path, "base_dir": os.path.dirname(path)}

    def api_save(self, body):
        path = os.path.abspath(body["path"])
        if not path.lower().endswith(".json"):
            path += ".json"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(body["project"], fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        self.settings.add_recent(path)
        return {"path": path, "base_dir": os.path.dirname(path)}

    def api_lut(self, body):
        return {"description": Lut(body["path"]).describe()}

    def api_reveal(self, body):
        path = body["path"]
        target = path if os.path.isdir(path) else os.path.dirname(path)
        if os.name == "nt":
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                os.startfile(target)  # noqa: S606 - opening a folder the user picked
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            opener = shutil.which("xdg-open")
            if opener:
                subprocess.Popen([opener, target])
        return {"ok": True}

    def api_heartbeat(self, _):
        self.last_seen = time.time()
        return {"ok": True, "busy": self.jobs.busy()}

    def api_shutdown(self, _):
        threading.Timer(0.3, self.server.shutdown).start()
        return {"ok": True}

    def api_command(self, body):
        """Run a feature command (scene detection, auto tone, ...) through the CLI in-process."""
        from ..cli import build_parser
        argv = [str(a) for a in body.get("argv", [])]
        allowed = {"scenes", "autotone", "colormatch", "loudness", "sync", "reframe", "transcribe", "paperedit",
                   "captions", "export-edl", "export-fcpxml", "export-otio", "import-edl", "markers", "timeline",
                   "colorspace", "probe", "lut"}
        if not argv or argv[0] not in allowed:
            raise RushesError("That command is not available from the Studio")
        import contextlib
        import io
        buf = io.StringIO()
        try:
            args = build_parser().parse_args(argv)
        except SystemExit:
            raise RushesError("Invalid command arguments") from None
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            args.func(args)
        return {"output": buf.getvalue()}

    GET = {"/api/info": "api_info", "/api/fs": "api_fs", "/api/jobs": "api_jobs"}
    POST = {"/api/probe": "api_probe", "/api/validate": "api_validate", "/api/frame": "api_frame",
            "/api/render": "api_render", "/api/cancel": "api_cancel", "/api/clear": "api_clear",
            "/api/open": "api_open", "/api/save": "api_save", "/api/lut": "api_lut", "/api/reveal": "api_reveal",
            "/api/heartbeat": "api_heartbeat", "/api/shutdown": "api_shutdown", "/api/command": "api_command"}

    # -- server -------------------------------------------------------------

    def make_handler(self):
        studio = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "RushesStudio"

            def log_message(self, fmt, *args):  # quiet
                pass

            def _host_ok(self):
                host = (self.headers.get("Host") or "").lower()
                return host in (f"127.0.0.1:{studio.port}", f"localhost:{studio.port}")

            def _send(self, code, body, ctype="application/json", extra=None):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _api(self, table, payload):
                path = urllib.parse.urlparse(self.path).path
                if self.headers.get("X-Rushes-Token") != studio.token:
                    return self._send(403, {"error": "bad token"})
                name = table.get(path)
                if not name:
                    return self._send(404, {"error": "unknown endpoint"})
                studio.last_seen = time.time()
                try:
                    result = getattr(studio, name)(payload)
                    self._send(200, result)
                except RushesError as exc:
                    self._send(400, {"error": str(exc)})
                except (KeyError, TypeError, ValueError) as exc:
                    self._send(400, {"error": f"Bad request: {exc}"})
                except Exception as exc:  # report, don't crash the server
                    self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

            def do_GET(self):
                if not self._host_ok():
                    return self._send(403, {"error": "bad host"})
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path.startswith("/api/"):
                    q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                    return self._api(studio.GET, q)
                if parsed.path == "/media":
                    return self._media(urllib.parse.parse_qs(parsed.query))
                return self._static(parsed.path)

            def do_POST(self):
                if not self._host_ok():
                    return self._send(403, {"error": "bad host"})
                length = int(self.headers.get("Content-Length") or 0)
                if length > 50 * 1024 * 1024:
                    return self._send(413, {"error": "request too large"})
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._send(400, {"error": "invalid JSON"})
                return self._api(studio.POST, payload)

            def _static(self, path):
                if path in ("", "/"):
                    path = "/index.html"
                full = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
                if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
                    return self._send(404, b"not found", "text/plain")
                ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/javascript",):
                    ctype += "; charset=utf-8"
                with open(full, "rb") as fh:
                    self._send(200, fh.read(), ctype)

            def _media(self, q):
                """Stream a rendered preview/media file to the <video> element (supports Range)."""
                if (q.get("t") or [""])[0] != studio.token:
                    return self._send(403, b"forbidden", "text/plain")
                path = (q.get("path") or [""])[0]
                if not os.path.isfile(path) or os.path.splitext(path)[1].lower() not in MEDIA_EXTS:
                    return self._send(404, b"not found", "text/plain")
                size = os.path.getsize(path)
                start, end = 0, size - 1
                rng = self.headers.get("Range")
                status = 200
                if rng and rng.startswith("bytes="):
                    a, _, b = rng[6:].partition("-")
                    start = int(a) if a else max(0, size - int(b))
                    end = int(b) if (a and b) else size - 1
                    end = min(end, size - 1)
                    status = 206
                length = max(0, end - start + 1)
                self.send_response(status)
                self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with open(path, "rb") as fh:
                    fh.seek(start)
                    remaining = length
                    try:
                        while remaining > 0:
                            chunk = fh.read(min(1 << 20, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        return Handler

    def start(self, port=0):
        self.server = ThreadingHTTPServer(("127.0.0.1", port), self.make_handler())
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    def watchdog(self):
        while True:
            time.sleep(5)
            if time.time() - self.last_seen > IDLE_TIMEOUT and not self.jobs.busy():
                self.server.shutdown()
                return


def self_test(studio):
    """Start the server, fetch the UI and the API, render a preview frame. Used by the Windows CI."""
    url = studio.start(0)
    base = f"http://127.0.0.1:{studio.port}"

    def call(path, payload=None):
        req = urllib.request.Request(base + path, headers={"X-Rushes-Token": studio.token,
                                                           "Content-Type": "application/json"},
                                     data=json.dumps(payload).encode() if payload is not None else None)
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    with urllib.request.urlopen(url, timeout=30) as resp:
        html = resp.read().decode("utf-8")
    assert "Rushes Studio" in html, "index page missing"
    info = call("/api/info")
    assert info["ffmpeg"], "ffmpeg not found"
    project = {"settings": {"width": 640, "height": 360, "fps": 24},
               "clips": [{"generator": "bars", "duration": 1, "color": {"saturation": 50}}],
               "titles": [{"text": "Self test", "start": 0, "duration": 1}]}
    frame = call("/api/frame", {"project": project, "base_dir": runtime.user_data_dir(), "time": 0.5,
                                "compare": True, "scopes": True})
    assert frame["image"].startswith("data:image/png;base64,"), "no preview frame"
    assert frame["scopes"].startswith("data:image/png;base64,"), "no scopes"
    print(f"Self-test OK: {info['ffmpeg']}")
    studio.server.shutdown()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rushes-studio", description="Rushes Studio desktop editor")
    parser.add_argument("--port", type=int, default=0, help="port to listen on (default: any free port)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    parser.add_argument("--self-test", action="store_true", help="start, check the UI and API, then exit")
    parser.add_argument("project", nargs="?", help="project file to open")
    args = parser.parse_args(argv)
    runtime.setup()
    studio = Studio()
    if args.self_test:
        try:
            return self_test(studio)
        except Exception as exc:
            print(f"Self-test FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    url = studio.start(args.port)
    if args.project:
        url += "&project=" + urllib.parse.quote(os.path.abspath(args.project))
    print(f"Rushes Studio is running at {url}")
    print("Close the browser tab to quit (the server stops after about a minute without a tab open).")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        studio.watchdog()
    except KeyboardInterrupt:
        pass
    return 0
