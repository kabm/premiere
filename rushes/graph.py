"""Turn a Project into ffmpeg inputs + a -filter_complex graph.

Video lives in 16-bit 4:4:4 YCbCr (yuv444p16le) between steps and is graded
in 16-bit RGB. Audio is 32-bit float at the project sample rate.
"""

import os
import shutil

from . import audiofx, captions, compositing, keyframes, timeline
from .color import SCALE_FLAGS, WORKING_YUV, build_color_chain, wrap_rgb
from .util import RushesError, escape_value, fmt_seconds as fs, uid

LAYOUTS = {1: "mono", 2: "stereo", 6: "5.1"}

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

TITLE_POSITIONS = {
    "center": ("(w-text_w)/2", "(h-text_h)/2"),
    "lower_third": ("w*0.07", "h*0.78"),
    "top": ("(w-text_w)/2", "h*0.08"),
    "bottom": ("(w-text_w)/2", "h*0.88-text_h"),
}


def default_font():
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            return f
    found = shutil.which("fc-match")
    if found:
        import subprocess
        out = subprocess.run([found, "-f", "%{file}", "sans:bold"], capture_output=True, text=True).stdout
        if out and os.path.exists(out):
            return out
    return None


def atempo_chain(speed):
    """atempo only accepts 0.5..2.0 per instance, so chain as many as needed."""
    parts = []
    while speed > 2.0:
        parts.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        parts.append("atempo=0.5")
        speed /= 0.5
    if abs(speed - 1.0) > 1e-6:
        parts.append(f"atempo={speed:.6f}")
    return parts


class GraphBuilder:
    def __init__(self, project, preset=None, stab_files=None, loudnorm_stats=None, draft=False, cache_dir=None):
        self.p = project
        self.cache_dir = cache_dir or project.cache_dir
        self.preset = preset or {}
        self.stab_files = stab_files or {}
        self.loudnorm_stats = loudnorm_stats
        self.draft = draft
        self.inputs = []        # list of argument lists
        self.chains = []        # filtergraph chains
        self._input_index = {}

    def color_ctx(self, item=None):
        """Context handed to colour hooks (see colormgmt / looks)."""
        item = item or {}
        return {"cache_dir": self.cache_dir, "base_dir": self.p.base_dir, "matrix": self.p.matrix,
                "source": item.get("source"), "in": item.get("in"), "src_len": item.get("src_len"),
                "fps": self.p.fps, "width": self.p.width, "height": self.p.height}

    def grade(self, cfg, item=None):
        return wrap_rgb(build_color_chain(cfg, self.p.base_dir, self.p.matrix, self.color_ctx(item)), self.p.matrix)

    # -- inputs -------------------------------------------------------------

    def _input(self, key, args):
        if key not in self._input_index:
            self._input_index[key] = len(self.inputs)
            self.inputs.append(args)
        return self._input_index[key]

    def media_input(self, item, length, key):
        """Input args for a clip/overlay; seeks with -ss so only the used range is decoded."""
        fps = str(self.p.fps)
        if item.get("generator"):
            gen = item["generator"]
            size = f"{self.p.width}x{self.p.height}"
            if gen == "bars":
                src = f"smptehdbars=s={size}:r={fps}:d={fs(length)}"
            elif gen == "testsrc":
                src = f"testsrc2=s={size}:r={fps}:d={fs(length)}"
            else:
                c = "0x" + gen[1:] if gen.startswith("#") else gen
                src = f"color=c={c}:s={size}:r={fps}:d={fs(length)}"
            return self._input(key, ["-f", "lavfi", "-i", src]), False
        if item["is_image"]:
            args = ["-loop", "1", "-framerate", fps, "-t", fs(length), "-i", item["source"]]
            return self._input(key, args), False
        args = []
        if item["in"] > 0:
            args += ["-ss", fs(item["in"])]
        args += ["-t", fs(length), "-i", item["source"]]
        return self._input(key, args), item["info"].has_audio

    # -- video --------------------------------------------------------------

    def conform(self, fit):
        W, H = self.p.width, self.p.height
        to = f"in_color_matrix=auto:out_color_matrix={self.p.matrix}:out_range=tv:{SCALE_FLAGS}"
        if fit == "stretch":
            return [f"scale={W}:{H}:{to}", f"format={WORKING_YUV}", "setsar=1"]
        if fit == "fill":
            return [f"scale={W}:{H}:force_original_aspect_ratio=increase:{to}", f"format={WORKING_YUV}",
                    f"crop={W}:{H}", "setsar=1"]
        return [f"scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2:{to}",
                f"format={WORKING_YUV}", f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black", "setsar=1"]

    def transform(self, clip):
        """Zoom / position / rotation / flip on a canvas-sized frame."""
        if clip.get("keyframes"):
            kf = keyframes.transform_filters(clip, self.p)
            if kf is not None:
                return kf
        W, H = self.p.width, self.p.height
        out = []
        zoom = float(clip.get("zoom", 100)) / 100.0
        px, py = (clip.get("position") or [0, 0])
        if zoom <= 0:
            raise RushesError("zoom must be positive")
        if zoom != 1 or px or py:
            sw, sh = int(round(W * zoom / 2)) * 2, int(round(H * zoom / 2)) * 2
            if zoom != 1:
                out.append(f"scale={sw}:{sh}:{SCALE_FLAGS}")
            cx, cy = (sw - W) // 2 - int(px), (sh - H) // 2 - int(py)
            pl, pt = max(0, -cx), max(0, -cy)
            pr, pb = max(0, cx + W - sw), max(0, cy + H - sh)
            if pl or pt or pr or pb:
                out.append(f"pad={sw + pl + pr}:{sh + pt + pb}:{pl}:{pt}:color=black")
            out.append(f"crop={W}:{H}:{cx + pl}:{cy + pt}")
        if clip.get("rotation"):
            out.append(f"rotate={float(clip['rotation'])}*PI/180:fillcolor=black")
        flip = clip.get("flip") or ""
        if "h" in flip:
            out.append("hflip")
        if "v" in flip:
            out.append("vflip")
        return out

    def source_crop(self, crop):
        """crop: {left, right, top, bottom} as percentages of the source frame."""
        if not crop:
            return []
        l, r = float(crop.get("left", 0)) / 100, float(crop.get("right", 0)) / 100
        t, b = float(crop.get("top", 0)) / 100, float(crop.get("bottom", 0)) / 100
        if l + r >= 1 or t + b >= 1:
            raise RushesError("crop removes the whole frame")
        return [f"crop=trunc(iw*{1 - l - r}/2)*2:trunc(ih*{1 - t - b}/2)*2:trunc(iw*{l}):trunc(ih*{t})"]

    def clip_video(self, i, clip, idx):
        fx = clip["effects"]
        chain = []
        if fx.get("stabilize"):
            trf = self.stab_files.get(i)
            if not trf:
                raise RushesError(f"clips[{i}]: stabilisation data missing (internal error)")
            smoothing = 15 if fx["stabilize"] is True else int(fx["stabilize"])
            chain += [f"vidstabtransform=input={escape_value(trf)}:smoothing={smoothing}:optzoom=1:interpol=bicubic"]
        chain.append("setpts=PTS-STARTPTS")
        if fx.get("denoise"):
            n = float(fx["denoise"]) / 25.0
            chain.append(f"hqdn3d={4 * n:.3f}:{3 * n:.3f}:{6 * n:.3f}:{4.5 * n:.3f}")
        if clip.get("reverse"):
            chain.append("reverse")
        speed = clip["speed"]
        if speed != 1:
            chain.append(f"setpts=(PTS-STARTPTS)/{speed}")
        fps = str(self.p.fps)
        if clip["interpolation"] == "frame_blending":
            chain.append(f"minterpolate=fps={fps}:mi_mode=blend")
        elif clip["interpolation"] == "optical_flow":
            chain.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
        chain.append(f"fps={fps}")
        chain += self.source_crop(clip.get("crop"))
        chain += self.conform(clip["fit"])
        chain += self.transform(clip)
        if fx.get("blur"):
            chain.append(f"gblur=sigma={float(fx['blur'])}")
        if fx.get("sharpen"):
            amt = min(3.0, float(fx["sharpen"]) / 50.0)
            chain.append(f"unsharp=5:5:{amt:.3f}:5:5:0")
        graded = self.grade(clip.get("color"), clip)
        if graded:
            chain.append(graded)
        dur = clip["duration"]
        if clip.get("fade_in"):
            chain.append(f"fade=t=in:st=0:d={fs(self.p.t(clip['fade_in']))}")
        if clip.get("fade_out"):
            d = self.p.t(clip["fade_out"])
            chain.append(f"fade=t=out:st={fs(max(0, dur - d))}:d={fs(d)}")
        chain += [f"tpad=stop_mode=clone:stop_duration={fs(1 / float(self.p.fps))}",
                  f"trim=duration={fs(dur)}", "setpts=PTS-STARTPTS"]
        label = uid("v")
        self.chains.append(f"[{idx}:v]" + ",".join(chain) + f"[{label}]")
        return label

    def overlay_video(self, i, ov, base):
        idx, _ = self.media_input(ov, ov["src_len"], ("overlay", i))
        W = self.p.width
        chain = ["setpts=PTS-STARTPTS", f"fps={self.p.fps}"]
        to = f"in_color_matrix=auto:out_color_matrix={self.p.matrix}:out_range=tv:{SCALE_FLAGS}"
        width = ov.get("width")
        if width is not None:
            px = float(width) * W if float(width) <= 1 else float(width)
            chain.append(f"scale={int(px) // 2 * 2}:-2:{to}")
        else:
            chain.append(f"scale={W}:{self.p.height}:force_original_aspect_ratio=decrease:force_divisible_by=2:{to}")
        chain += ["setsar=1", f"format={WORKING_YUV}"]
        graded = self.grade(ov.get("color"), ov)
        if graded:
            chain.append(graded)
        key = ov.get("chroma_key")
        if key:
            if isinstance(key, str):
                key = {"color": key}
            c = key.get("color", "#00FF00").replace("#", "0x")
            chain.append(f"chromakey=color={c}:similarity={float(key.get('similarity', 0.12))}:"
                         f"blend={float(key.get('blend', 0.04))}")
        chain.append("format=yuva444p16le")
        if ov["opacity"] < 1:
            chain.append(f"colorchannelmixer=aa={ov['opacity']}")
        dur, start = ov["duration"], ov["start"]
        if ov.get("fade_in"):
            chain.append(f"fade=t=in:st=0:d={fs(self.p.t(ov['fade_in']))}:alpha=1")
        if ov.get("fade_out"):
            d = self.p.t(ov["fade_out"])
            chain.append(f"fade=t=out:st={fs(max(0, dur - d))}:d={fs(d)}:alpha=1")
        chain += [f"trim=duration={fs(dur)}", f"setpts=PTS-STARTPTS+{fs(start)}/TB"]
        ol = uid("ov")
        self.chains.append(f"[{idx}:v]" + ",".join(chain) + f"[{ol}]")
        out = uid("v")
        self.chains.append(compositing.composite(base, ol, ov, self.p, out))
        return out

    def title_filter(self, t):
        font = t.get("font")
        font = self.p.path(font) if font else default_font()
        size = t.get("size", int(self.p.height / 14))
        pos = t.get("position", "center")
        if pos not in TITLE_POSITIONS:
            raise RushesError(f"title position must be one of {sorted(TITLE_POSITIONS)}")
        x, y = TITLE_POSITIONS[pos]
        x, y = t.get("x", x), t.get("y", y)
        s, e = t["start"], t["start"] + t["duration"]
        f = min(float(t.get("fade", 0.5)), t["duration"] / 2)
        opts = [f"text={escape_value(t['text'])}", "expansion=none", f"fontsize={size}",
                f"fontcolor={escape_value(t.get('color', 'white'))}", f"x={escape_value(x)}", f"y={escape_value(y)}",
                f"enable={escape_value(f'between(t,{fs(s)},{fs(e)})')}"]
        if font:
            opts.insert(0, f"fontfile={escape_value(font)}")
        if f > 0:
            alpha = f"if(lt(t,{fs(s + f)}),(t-{fs(s)})/{fs(f)},if(gt(t,{fs(e - f)}),({fs(e)}-t)/{fs(f)},1))"
            opts.append(f"alpha={escape_value(alpha)}")
        if t.get("box"):
            opts += ["box=1", f"boxcolor={escape_value(t.get('box_color', 'black@0.55'))}",
                     f"boxborderw={int(size * 0.4)}"]
        if t.get("shadow", True):
            opts += ["shadowx=2", "shadowy=2", "shadowcolor=black@0.6"]
        return "drawtext=" + ":".join(opts)

    def output_format(self):
        pix = self.preset.get("pix_fmt") or "yuv422p10le"
        color = self.preset.get("color", {})
        matrix = "bt2020" if color.get("matrix", "").startswith("bt2020") else self.p.matrix
        chain = []
        if self.draft:
            chain.append(f"scale=iw/2:-2:{SCALE_FLAGS}")
        if pix.startswith(("rgb", "gbr", "bgr")):
            chain.append(f"scale=in_color_matrix={matrix}:in_range=tv:{SCALE_FLAGS}")
            chain.append(f"format={pix}")
        else:
            chain.append(f"scale=out_color_matrix={matrix}:out_range=tv:{SCALE_FLAGS}")
            chain.append(f"format={pix}")
            if color:
                chain.append(f"setparams=range=tv:color_primaries={color['primaries']}:"
                             f"color_trc={color['trc']}:colorspace={color['matrix']}")
        return chain

    # -- audio --------------------------------------------------------------

    def aformat(self):
        return (f"aresample={self.p.sample_rate},aformat=sample_fmts=fltp:sample_rates={self.p.sample_rate}:"
                f"channel_layouts={LAYOUTS[self.p.channels]}")

    def silence(self, dur):
        return f"anullsrc=r={self.p.sample_rate}:cl={LAYOUTS[self.p.channels]},atrim=duration={fs(dur)}"

    def clip_audio(self, clip, idx, has_audio):
        dur = clip["duration"]
        a = clip["audio"]
        label = uid("a")
        if not has_audio or a.get("mute"):
            self.chains.append(self.silence(dur) + f"[{label}]")
            return label
        chain = ["asetpts=PTS-STARTPTS", self.aformat()]
        if clip.get("reverse"):
            chain.append("areverse")
        chain += atempo_chain(clip["speed"])
        chain += audiofx.build(a.get("fx"), self.p)
        if a.get("volume_db"):
            chain.append(f"volume={float(a['volume_db'])}dB")
        if a.get("fade_in"):
            chain.append(f"afade=t=in:st=0:d={fs(self.p.t(a['fade_in']))}")
        if a.get("fade_out"):
            d = self.p.t(a["fade_out"])
            chain.append(f"afade=t=out:st={fs(max(0, dur - d))}:d={fs(d)}")
        chain += ["apad", f"atrim=duration={fs(dur)}", "asetpts=PTS-STARTPTS"]
        self.chains.append(f"[{idx}:a]" + ",".join(chain) + f"[{label}]")
        return label

    def audio_track(self, i, tr):
        total = self.p.duration
        length = tr["length"] if tr["length"] is not None else max(0.0, total - tr["start"])
        args = []
        if tr.get("loop"):
            args += ["-stream_loop", "-1"]
        if tr["in"] > 0:
            args += ["-ss", fs(tr["in"])]
        args += ["-t", fs(length), "-i", tr["source"]]
        idx = self._input(("audio", i), args)
        chain = ["asetpts=PTS-STARTPTS", self.aformat()]
        chain += audiofx.build(tr.get("fx"), self.p)
        if tr.get("volume_db"):
            chain.append(f"volume={float(tr['volume_db'])}dB")
        if tr.get("fade_in"):
            chain.append(f"afade=t=in:st=0:d={fs(self.p.t(tr['fade_in']))}")
        if tr.get("fade_out"):
            d = self.p.t(tr["fade_out"])
            chain.append(f"afade=t=out:st={fs(max(0, length - d))}:d={fs(d)}")
        if tr["start"] > 0:
            chain.append(f"adelay=delays={int(round(tr['start'] * 1000))}:all=1")
        label = uid("t")
        self.chains.append(f"[{idx}:a]" + ",".join(chain) + f"[{label}]")
        return label

    def master_audio(self, dialog):
        p = self.p
        tracks = [(self.audio_track(i, tr), tr) for i, tr in enumerate(p.audio_tracks)]
        ducked = [lbl for lbl, tr in tracks if tr.get("duck")]
        plain = [lbl for lbl, tr in tracks if not tr.get("duck")]
        bus = [dialog]
        if ducked:
            music = ducked[0]
            if len(ducked) > 1:
                music = uid("m")
                self.chains.append("".join(f"[{l}]" for l in ducked) +
                                   f"amix=inputs={len(ducked)}:duration=longest:normalize=0[{music}]")
            dlg, key, out = uid("d"), uid("k"), uid("dk")
            amount = next(tr.get("duck") for _, tr in tracks if tr.get("duck"))
            ratio = 8 if amount is True else max(1.5, float(amount))
            self.chains.append(f"[{dialog}]asplit=2[{dlg}][{key}]")
            self.chains.append(f"[{music}][{key}]sidechaincompress=threshold=0.02:ratio={ratio}:"
                               f"attack=20:release=600:makeup=1[{out}]")
            bus = [dlg, out]
        bus += plain
        mixed = bus[0]
        if len(bus) > 1:
            mixed = uid("mix")
            self.chains.append("".join(f"[{l}]" for l in bus) +
                               f"amix=inputs={len(bus)}:duration=first:normalize=0[{mixed}]")
        chain = []
        m = p.master
        chain += audiofx.build(m.get("audio_fx"), p)
        if m.get("volume_db"):
            chain.append(f"volume={float(m['volume_db'])}dB")
        loud = m.get("loudness")
        if loud:
            if loud is True:
                loud = {}
            target = float(loud.get("target", -14))
            tp = float(loud.get("true_peak", -1))
            lra = float(loud.get("lra", 11))
            ln = f"loudnorm=I={target}:TP={tp}:LRA={lra}"
            if self.loudnorm_stats == "measure":
                ln += ":print_format=json"
            elif self.loudnorm_stats:
                s = self.loudnorm_stats
                ln += (f":measured_I={s['input_i']}:measured_TP={s['input_tp']}:measured_LRA={s['input_lra']}"
                       f":measured_thresh={s['input_thresh']}:offset={s['target_offset']}:linear=true")
            chain += [ln, f"aresample={p.sample_rate}"]
        if m.get("fade_in"):
            chain.append(f"afade=t=in:st=0:d={fs(p.t(m['fade_in']))}")
        if m.get("fade_out"):
            d = p.t(m["fade_out"])
            chain.append(f"afade=t=out:st={fs(max(0, p.duration - d))}:d={fs(d)}")
        chain += [f"atrim=duration={fs(p.duration)}", "asetpts=PTS-STARTPTS"]
        self.chains.append(f"[{mixed}]" + ",".join(chain) + "[aout]")

    # -- assembly -----------------------------------------------------------

    def build(self, video=True, audio=True):
        p = self.p
        vlabels, alabels = [], []
        for i, clip in enumerate(p.clips):
            length = clip["src_len"]
            idx, has_audio = self.media_input(clip, length, ("clip", i))
            if video:
                vlabels.append(self.clip_video(i, clip, idx))
            if audio:
                alabels.append(self.clip_audio(clip, idx, has_audio))

        # Join V1 / A1 with transitions.
        v, a = (vlabels[0] if video else None), (alabels[0] if audio else None)
        acc = p.clips[0]["duration"]
        for i in range(1, len(p.clips)):
            clip = p.clips[i]
            tr = clip["transition"]
            nv, na = uid("v"), uid("a")
            if tr:
                d = tr["duration"]
                if video:
                    self.chains.append(f"[{v}][{vlabels[i]}]xfade=transition={tr['type']}:duration={fs(d)}:"
                                       f"offset={fs(acc - d)}[{nv}]")
                if audio:
                    self.chains.append(f"[{a}][{alabels[i]}]acrossfade=d={fs(d)}:c1=qsin:c2=qsin[{na}]")
                acc += clip["duration"] - d
            else:
                if video and audio:
                    self.chains.append(f"[{v}][{a}][{vlabels[i]}][{alabels[i]}]concat=n=2:v=1:a=1[{nv}][{na}]")
                elif video:
                    self.chains.append(f"[{v}][{vlabels[i]}]concat=n=2:v=1:a=0[{nv}]")
                else:
                    self.chains.append(f"[{a}][{alabels[i]}]concat=n=2:v=0:a=1[{na}]")
                acc += clip["duration"]
            v, a = nv, na

        if video:
            for i, ov in enumerate(p.overlays):
                v = self.overlay_video(i, ov, v)
            v = timeline.adjustment_layers(self, v)
            chain = [self.title_filter(t) for t in p.titles]
            chain += captions.burn_filters(p)
            graded = self.grade(p.master.get("color"))
            if graded:
                chain.append(graded)
            if p.master.get("fade_in"):
                chain.append(f"fade=t=in:st=0:d={fs(p.t(p.master['fade_in']))}")
            if p.master.get("fade_out"):
                d = p.t(p.master["fade_out"])
                chain.append(f"fade=t=out:st={fs(max(0, p.duration - d))}:d={fs(d)}")
            chain += [f"trim=duration={fs(p.duration)}", "setpts=PTS-STARTPTS"]
            chain += self.output_format()
            self.chains.append(f"[{v}]" + ",".join(chain) + "[vout]")
        if audio:
            self.master_audio(a)

        args = []
        for inp in self.inputs:
            args += inp
        return args, ";\n".join(self.chains)
