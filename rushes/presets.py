"""Export presets, from mastering intermediates down to web delivery.

Every preset is rendered from the same 16-bit 4:4:4 timeline, so the only
quality loss is whatever the target codec itself throws away.
"""

from .util import RushesError

BT709 = {"primaries": "bt709", "trc": "bt709", "matrix": "bt709"}
BT2020_PQ = {"primaries": "bt2020", "trc": "smpte2084", "matrix": "bt2020nc"}

PCM24 = ["-c:a", "pcm_s24le"]
AAC320 = ["-c:a", "aac", "-b:a", "320k"]
FLAC = ["-c:a", "flac", "-compression_level", "8"]


def _p(category, description, ext, video, pix_fmt, audio, color=BT709, sequence=False, audio_only=False):
    return {
        "category": category,
        "description": description,
        "ext": ext,
        "video": video,
        "pix_fmt": pix_fmt,
        "audio": audio,
        "color": color,
        "sequence": sequence,
        "audio_only": audio_only,
    }


PRESETS = {
    # Apple ProRes (prores_ks encoder)
    "prores_4444xq": _p("Mastering", "ProRes 4444 XQ, 10-bit 4:4:4 at the highest ProRes data rate", ".mov",
                        ["-c:v", "prores_ks", "-profile:v", "5", "-vendor", "apl0", "-bits_per_mb", "8000"],
                        "yuv444p10le", PCM24),
    "prores_4444": _p("Mastering", "ProRes 4444, 10-bit 4:4:4", ".mov",
                      ["-c:v", "prores_ks", "-profile:v", "4", "-vendor", "apl0", "-bits_per_mb", "8000"],
                      "yuv444p10le", PCM24),
    "prores_422hq": _p("Mastering", "ProRes 422 HQ, 10-bit 4:2:2, standard broadcast master", ".mov",
                       ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0"], "yuv422p10le", PCM24),
    "prores_422": _p("Intermediate", "ProRes 422, 10-bit 4:2:2", ".mov",
                     ["-c:v", "prores_ks", "-profile:v", "2", "-vendor", "apl0"], "yuv422p10le", PCM24),
    "prores_lt": _p("Intermediate", "ProRes 422 LT, 10-bit 4:2:2, lighter files", ".mov",
                    ["-c:v", "prores_ks", "-profile:v", "1", "-vendor", "apl0"], "yuv422p10le", PCM24),
    "prores_proxy": _p("Proxy", "ProRes 422 Proxy, for offline editing", ".mov",
                       ["-c:v", "prores_ks", "-profile:v", "0", "-vendor", "apl0"], "yuv422p10le", PCM24),
    # Avid DNxHR
    "dnxhr_444": _p("Mastering", "DNxHR 444, 10-bit 4:4:4 (Avid finishing)", ".mov",
                    ["-c:v", "dnxhd", "-profile:v", "dnxhr_444"], "yuv444p10le", PCM24),
    "dnxhr_hqx": _p("Mastering", "DNxHR HQX, 10-bit 4:2:2", ".mov",
                    ["-c:v", "dnxhd", "-profile:v", "dnxhr_hqx"], "yuv422p10le", PCM24),
    "dnxhr_hq": _p("Intermediate", "DNxHR HQ, 8-bit 4:2:2", ".mov",
                   ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq"], "yuv422p", PCM24),
    "dnxhr_lb": _p("Proxy", "DNxHR LB, low-bandwidth proxy", ".mov",
                   ["-c:v", "dnxhd", "-profile:v", "dnxhr_lb"], "yuv422p", PCM24),
    # GoPro CineForm
    "cineform_film": _p("Intermediate", "CineForm 10-bit 4:2:2, Film Scan 3+ quality", ".mov",
                        ["-c:v", "cfhd", "-quality", "film3+"], "yuv422p10le", PCM24),
    # Lossless archive
    "ffv1_lossless": _p("Archive", "FFV1 lossless, 16-bit 4:4:4, bit-exact copy of the timeline", ".mkv",
                        ["-c:v", "ffv1", "-level", "3", "-coder", "1", "-context", "1", "-g", "1",
                         "-slices", "16", "-slicecrc", "1"], "yuv444p16le", FLAC),
    # Delivery
    "h264_master": _p("Delivery", "H.264 High, CRF 12, near-transparent mezzanine", ".mp4",
                      ["-c:v", "libx264", "-preset", "slow", "-crf", "12", "-profile:v", "high",
                       "-movflags", "+faststart"], "yuv420p", AAC320),
    "h264_web": _p("Delivery", "H.264 High, CRF 18, YouTube/Vimeo upload", ".mp4",
                   ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-profile:v", "high",
                    "-bf", "2", "-movflags", "+faststart"], "yuv420p", AAC320),
    "hevc_10bit": _p("Delivery", "HEVC Main10, CRF 14, 10-bit SDR", ".mp4",
                     ["-c:v", "libx265", "-preset", "slow", "-crf", "14", "-profile:v", "main10",
                      "-tag:v", "hvc1", "-x265-params", "log-level=error", "-movflags", "+faststart"],
                     "yuv420p10le", AAC320),
    "hevc_hdr10": _p("Delivery", "HEVC Main10 HDR10 (grade must output Rec.2020 PQ)", ".mp4",
                     ["-c:v", "libx265", "-preset", "slow", "-crf", "14", "-profile:v", "main10",
                      "-tag:v", "hvc1", "-movflags", "+faststart", "-x265-params",
                      "log-level=error:hdr10=1:hdr10-opt=1:repeat-headers=1:colorprim=bt2020:"
                      "transfer=smpte2084:colormatrix=bt2020nc:"
                      "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):"
                      "max-cll=1000,400"],
                     "yuv420p10le", AAC320, color=BT2020_PQ),
    "av1_10bit": _p("Delivery", "AV1 10-bit (SVT-AV1), CRF 18, smallest high-quality files", ".mp4",
                    ["-c:v", "libsvtav1", "-preset", "4", "-crf", "18", "-svtav1-params", "tune=0",
                     "-movflags", "+faststart"], "yuv420p10le", AAC320),
    # Image sequences (+ a 24-bit WAV next to them)
    "tiff_16bit": _p("Image sequence", "16-bit RGB TIFF sequence (deflate)", ".tiff",
                     ["-c:v", "tiff", "-compression_algo", "deflate"], "rgb48le", PCM24, sequence=True),
    "exr_half": _p("Image sequence", "OpenEXR half-float sequence (ZIP16)", ".exr",
                   ["-c:v", "exr", "-format", "1", "-compression", "3"], "gbrpf32le", PCM24, sequence=True),
    "dpx_10bit": _p("Image sequence", "10-bit DPX sequence (film/DI)", ".dpx",
                    ["-c:v", "dpx"], "gbrp10le", PCM24, sequence=True),
    "png_16bit": _p("Image sequence", "16-bit PNG sequence", ".png",
                    ["-c:v", "png"], "rgb48be", PCM24, sequence=True),
    # Audio only
    "wav_24bit": _p("Audio", "Broadcast WAV, 24-bit 48 kHz", ".wav", [], None, PCM24, audio_only=True),
    "flac": _p("Audio", "FLAC lossless audio", ".flac", [], None, FLAC, audio_only=True),
}


def get_preset(name):
    try:
        return PRESETS[name]
    except KeyError:
        raise RushesError(f"Unknown preset '{name}'. Run 'rushes presets' to list them.") from None


def list_presets():
    rows = []
    for category in ("Mastering", "Intermediate", "Archive", "Delivery", "Image sequence", "Proxy", "Audio"):
        rows.append(f"\n{category}")
        for name, p in PRESETS.items():
            if p["category"] == category:
                rows.append(f"  {name:<15} {p['ext']:<6} {p['description']}")
    return "\n".join(rows).lstrip("\n")
