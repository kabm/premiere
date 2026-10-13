import json
import os
import shutil
import subprocess
import tempfile
import unittest

from rushes.color import tone_curve
from rushes.lut import Lut
from rushes.probe import probe
from rushes.project import Project
from rushes.render import render
from rushes.util import RushesError, escape_value, parse_time
from tests.fixtures import make_fixtures

FIX = os.path.join(tempfile.gettempdir(), "rushes_test_fixtures")


def frame_mean(path, t=0.5):
    """Average R, G, B (0..255) of a frame."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1",
                          "-vf", "scale=1:1:flags=area,format=rgb24", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return tuple(out[:3])


class UnitTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(parse_time("1:02.5"), 62.5)
        self.assertEqual(parse_time("01:00:00:12", 24), 3600.5)
        self.assertEqual(parse_time(3), 3.0)

    def test_escape(self):
        self.assertEqual(escape_value("C:\\a b's,[x]"), "C\\\\:\\\\\\\\a b\\\\\\'s\\,\\[x\\]")

    def test_tone_curve_monotonic(self):
        for args in ({"contrast": 100}, {"contrast": -100}, {"blacks": -100, "whites": 100}, {"shadows": 100}):
            ys = [float(p.split("/")[1]) for p in tone_curve(**args).split()]
            self.assertEqual(ys, sorted(ys))
            self.assertTrue(all(0 <= y <= 1 for y in ys))

    def test_bad_lut(self):
        with tempfile.NamedTemporaryFile("w", suffix=".cube", delete=False) as fh:
            fh.write("LUT_3D_SIZE 2\n0 0 0\n")
        with self.assertRaises(RushesError):
            Lut(fh.name)
        os.unlink(fh.name)


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = make_fixtures(FIX)
        cls.out = tempfile.mkdtemp(prefix="rushes_out_")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def project(self, **extra):
        f = self.f
        data = {
            "settings": {"title": "Test Edit", "width": 640, "height": 360, "fps": 24, "start_timecode": "01:00:00:00"},
            "clips": [
                {"source": f["clip_a"], "in": 0.5, "out": 2.5,
                 "color": {"exposure": 0.2, "contrast": 20, "temperature": 5600, "saturation": 120,
                           "lut": f["warm"], "lut_intensity": 60, "shadow_tint": [0, 0, 0.05]},
                 "effects": {"denoise": 20, "sharpen": 20}, "fade_in": 0.25},
                {"source": f["clip_b"], "in": 0, "duration": 1.5, "speed": 0.5,
                 "transition": {"type": "cross_dissolve", "duration": 0.5},
                 "color": {"input_lut": f["log_to_709"], "lut": f["warm"]},
                 "zoom": 120, "position": [20, -10]},
                {"generator": "bars", "duration": 1, "transition": {"type": "wipeleft", "duration": 0.25}},
                {"source": f["logo"], "duration": 1, "fit": "fill", "rotation": 5, "flip": "h"},
            ],
            "overlays": [{"source": f["logo"], "start": 0.5, "duration": 1.5, "width": 0.2, "x": "right",
                          "y": "top", "opacity": 0.8, "fade_in": 0.25, "fade_out": 0.25}],
            "titles": [{"text": "Hello: 'world', [1]", "start": 0.2, "duration": 1.5, "position": "lower_third",
                        "box": True}],
            "audio": [{"source": f["music"], "start": 0.5, "volume_db": -6, "fade_in": 0.5, "duck": True}],
            "master": {"loudness": {"target": -16}, "fade_out": 0.5},
        }
        data.update(extra)
        return Project(data, FIX)

    def test_timeline_duration(self):
        p = self.project()
        # 2 + 1.5 (0.75s at half speed) - 0.5 + 1 - 0.25 + 1
        self.assertAlmostEqual(p.duration, 4.75, places=3)

    def check_video(self, path, codec, pix_fmt, duration):
        info = probe(path)
        self.assertEqual(info.video["codec_name"], codec)
        self.assertEqual(info.video["pix_fmt"], pix_fmt)
        self.assertEqual((info.width, info.height), (640, 360))
        self.assertAlmostEqual(info.duration, duration, delta=0.1)
        return info

    def test_full_project_prores_4444xq(self):
        p = self.project()
        out = render(p, "prores_4444xq", os.path.join(self.out, "full.mov"))[0]
        # ProRes 4444 streams always decode as 12-bit
        info = self.check_video(out, "prores", "yuv444p12le", p.duration)
        self.assertEqual(info.video.get("profile"), "XQ")
        self.assertEqual(info.audio["codec_name"], "pcm_s24le")
        self.assertEqual(info.video.get("color_primaries"), "bt709")
        tags = info.video.get("tags", {})
        self.assertEqual(tags.get("timecode"), "01:00:00:00")

    def test_delivery_presets(self):
        p = self.project(clips=[{"source": self.f["clip_a"], "out": 1, "color": {"lut": self.f["warm"]}}],
                         overlays=[], titles=[], audio=[])
        for preset, codec, pix in (("h264_master", "h264", "yuv420p"), ("hevc_10bit", "hevc", "yuv420p10le"),
                                   ("dnxhr_444", "dnxhd", "yuv444p10le"), ("prores_422hq", "prores", "yuv422p10le"), ("ffv1_lossless", "ffv1", "yuv444p16le"),
                                   ("av1_10bit", "av1", "yuv420p10le"), ("cineform_film", "cfhd", "yuv422p10le")):
            with self.subTest(preset=preset):
                out = render(p, preset, os.path.join(self.out, "d_" + preset))[0]
                self.check_video(out, codec, pix, 1.0)

    def test_image_sequence(self):
        p = self.project(clips=[{"source": self.f["clip_a"], "out": 0.25}], overlays=[], titles=[], audio=[])
        for preset, ext in (("tiff_16bit", ".tiff"), ("exr_half", ".exr"), ("dpx_10bit", ".dpx")):
            with self.subTest(preset=preset):
                out_dir = os.path.join(self.out, "seq_" + preset)
                render(p, preset, out_dir)
                frames = [n for n in os.listdir(out_dir) if n.endswith(ext)]
                self.assertEqual(len(frames), 6)
                self.assertTrue(os.path.exists(os.path.join(out_dir, "seq_" + preset + ".wav")))

    def test_loudness_target(self):
        p = self.project(clips=[{"source": self.f["clip_a"]}], overlays=[], titles=[], audio=[],
                         master={"loudness": {"target": -20, "true_peak": -2}})
        out = render(p, "wav_24bit", os.path.join(self.out, "loud.wav"))[0]
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", out, "-af", "ebur128", "-f", "null", "-"],
                           capture_output=True, text=True)
        summary = r.stderr[r.stderr.rfind("Summary"):]
        integrated = float(summary.split("I:")[1].split("LUFS")[0])
        self.assertAlmostEqual(integrated, -20, delta=1.0)

    def test_lut_changes_colour(self):
        base = dict(overlays=[], titles=[], audio=[], master={})
        p1 = self.project(clips=[{"source": self.f["clip_a"], "out": 1}], **base)
        p2 = self.project(clips=[{"source": self.f["clip_a"], "out": 1, "color": {"lut": self.f["warm"]}}], **base)
        p3 = self.project(clips=[{"source": self.f["clip_a"], "out": 1, "color": {"lut": self.f["identity"]}}], **base)
        a = frame_mean(render(p1, "ffv1_lossless", os.path.join(self.out, "lut1"))[0])
        b = frame_mean(render(p2, "ffv1_lossless", os.path.join(self.out, "lut2"))[0])
        c = frame_mean(render(p3, "ffv1_lossless", os.path.join(self.out, "lut3"))[0])
        self.assertGreater(b[0] - b[2], a[0] - a[2] + 10)  # warmer: more red relative to blue
        for x, y in zip(a, c):
            self.assertLessEqual(abs(x - y), 1)  # identity LUT is transparent

    def test_stabilize_and_speed(self):
        p = self.project(clips=[{"source": self.f["clip_a"], "out": 1, "speed": 2, "interpolation": "frame_blending",
                                 "effects": {"stabilize": 10}}], overlays=[], titles=[], audio=[], master={})
        out = render(p, "prores_422hq", os.path.join(self.out, "stab.mov"))[0]
        self.check_video(out, "prores", "yuv422p10le", 0.5)

    def test_cli(self):
        from rushes.cli import main
        f = self.f
        proj = os.path.join(self.out, "cli_project.json")
        with open(proj, "w") as fh:
            json.dump({"settings": {"width": 640, "height": 360, "fps": 24},
                       "clips": [{"source": f["clip_a"], "out": 0.5}],
                       "export": {"preset": "h264_web", "output": "cli_render.mp4"}}, fh)
        self.assertEqual(main(["render", proj]), 0)
        self.assertTrue(os.path.exists(os.path.join(self.out, "cli_render.mp4")))
        self.assertEqual(main(["grade", f["clip_b"], "--lut", f["warm"], "--exposure", "0.5", "--out", "1",
                               "--preset", "prores_422", "-o", os.path.join(self.out, "graded.mov")]), 0)
        self.assertEqual(probe(os.path.join(self.out, "graded.mov")).width, 800)
        self.assertEqual(main(["still", f["clip_a"], "-t", "1", "--lut", f["warm"], "--compare",
                               "-o", os.path.join(self.out, "still.png")]), 0)
        self.assertEqual(probe(os.path.join(self.out, "still.png")).width, 1280)
        self.assertEqual(main(["scopes", f["clip_a"], "-o", os.path.join(self.out, "scopes.png")]), 0)
        self.assertEqual(main(["proxy", f["clip_a"], "-o", os.path.join(self.out, "proxies"), "--height", "180"]), 0)
        self.assertEqual(probe(os.path.join(self.out, "proxies", "clip_a_proxy.mov")).height, 180)
        self.assertEqual(main(["lut", f["warm"]]), 0)
        self.assertEqual(main(["render", proj, "--preset", "nope"]), 1)


if __name__ == "__main__":
    unittest.main()
