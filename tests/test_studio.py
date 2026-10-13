"""Rushes Studio server: API, security checks and the render queue."""

import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from rushes.studio.app import Studio
from tests.fixtures import make_fixtures

FIX = os.path.join(tempfile.gettempdir(), "rushes_test_fixtures")


class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = make_fixtures(FIX)
        cls.tmp = tempfile.mkdtemp(prefix="rushes_studio_")
        cls.studio = Studio()
        cls.url = cls.studio.start(0)
        cls.base = f"http://127.0.0.1:{cls.studio.port}"

    @classmethod
    def tearDownClass(cls):
        cls.studio.server.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, path, payload=None, token=True, host=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Rushes-Token"] = self.studio.token
        if host:
            headers["Host"] = host
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    def project(self):
        return {"settings": {"width": 640, "height": 360, "fps": 24},
                "clips": [{"source": self.f["clip_a"], "out": 1, "color": {"lut": self.f["warm"]}},
                          {"generator": "bars", "duration": 1, "transition": {"type": "cross_dissolve", "duration": 0.5}}],
                "titles": [{"text": "Hi", "start": 0.2, "duration": 1}]}

    def test_index_and_info(self):
        with urllib.request.urlopen(self.url, timeout=30) as resp:
            self.assertIn("Rushes Studio", resp.read().decode())
        info = self.call("/api/info")
        self.assertTrue(info["ffmpeg"])
        self.assertIn("prores_4444xq", [p["name"] for p in info["presets"]])

    def test_security(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.call("/api/info", token=False)
        self.assertEqual(ctx.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.call("/api/info", host="evil.example:80")
        self.assertEqual(ctx.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/../rushes/cli.py", timeout=10)
        self.assertEqual(ctx.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/media?t=wrong&path=" + self.f["clip_a"], timeout=10)
        self.assertEqual(ctx.exception.code, 403)

    def test_fs_probe_validate(self):
        listing = self.call("/api/fs?path=" + urllib.request.quote(FIX))
        kinds = {e["name"]: e.get("kind") for e in listing["entries"]}
        self.assertEqual(kinds["clip_a.mov"], "video")
        self.assertEqual(kinds["warm look.cube"], "lut")
        info = self.call("/api/probe", {"path": self.f["clip_a"]})
        self.assertAlmostEqual(info["duration"], 3.0, places=1)
        lay = self.call("/api/validate", {"project": self.project(), "base_dir": FIX})
        self.assertAlmostEqual(lay["duration"], 1.5, places=3)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.call("/api/validate", {"project": {"clips": []}, "base_dir": FIX})
        self.assertEqual(ctx.exception.code, 400)

    def test_frame_compare_scopes(self):
        res = self.call("/api/frame", {"project": self.project(), "base_dir": FIX, "time": 1.2,
                                       "compare": True, "scopes": True, "width": 320})
        self.assertEqual(res["clip_index"], 1)
        for key in ("image", "scopes"):
            self.assertTrue(res[key].startswith("data:image/png;base64,"))

    def test_save_open_and_render_queue(self):
        path = os.path.join(self.tmp, "proj.json")
        saved = self.call("/api/save", {"path": path, "project": self.project()})
        self.assertEqual(saved["path"], path)
        opened = self.call("/api/open", {"path": path})
        self.assertEqual(len(opened["project"]["clips"]), 2)
        out = os.path.join(self.tmp, "out.mp4")
        job = self.call("/api/render", {"project": self.project(), "base_dir": FIX, "preset": "h264_web",
                                        "output": out})
        deadline = time.time() + 120
        while time.time() < deadline:
            jobs = {j["id"]: j for j in self.call("/api/jobs")["jobs"]}
            if jobs[job["id"]]["status"] in ("done", "failed"):
                break
            time.sleep(0.3)
        self.assertEqual(jobs[job["id"]]["status"], "done", jobs[job["id"]].get("error"))
        self.assertTrue(os.path.exists(out))
        # the finished file streams with Range support
        req = urllib.request.Request(f"{self.base}/media?t={self.studio.token}&path={urllib.request.quote(out)}",
                                     headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 206)
            self.assertEqual(len(resp.read()), 100)

    def test_bad_render_reports_error(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.call("/api/render", {"project": self.project(), "base_dir": FIX, "preset": "no_such",
                                      "output": os.path.join(self.tmp, "x.mp4")})
        self.assertIn(ctx.exception.code, (400,))


if __name__ == "__main__":
    unittest.main()
