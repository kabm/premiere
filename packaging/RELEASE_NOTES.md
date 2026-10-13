## Install

1. Download **RushesSetup-x.y.z-x64.exe** below and run it. It doesn't need admin rights: choose
   "Install for me only". FFmpeg is included, so there's nothing else to install.
2. Windows may say "Windows protected your PC" because the installer isn't code-signed.
   Click **More info → Run anyway**.
3. Start **Rushes Studio** from the Start menu. The editor opens in your web browser and runs
   only on your computer.

Prefer no installer? Download the **portable zip**, unzip it anywhere and run `Rushes Studio.exe`.

## What's inside

- **Rushes Studio:** media browser, timeline (drag, trim, split, undo), Lumetri-style colour with
  your LUTs (.cube/.3dl/.dat/.m3d/.csp), basic correction, wheels and curves, effects, titles,
  overlays, music with auto-ducking, loudness normalisation, graded preview frames with
  before/after and scopes, quick preview playback, and an export queue.
- **Exports:** ProRes 4444 XQ / 4444 / 422 HQ, DNxHR 444 / HQX, CineForm, FFV1 16-bit lossless,
  H.264, 10-bit HEVC, HDR10, AV1, and 16-bit TIFF / EXR / DPX image sequences.
- **`rushes` command-line tool** for batch grading, proxies and project renders. Tick "Add to PATH"
  in the installer to use it from a terminal.

Every build is tested on Windows by the project's CI: the full test suite, the installed
programs, and a silent install and uninstall.

Camera RAW formats (BRAW, R3D, ARRIRAW, ProRes RAW) need converting to ProRes or DPX/EXR with the
camera maker's free tool first. Log footage in ProRes, H.264/H.265, XAVC, MXF and similar works directly.
