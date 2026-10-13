"""Entry point for the desktop app (Rushes Studio.exe): starts the local UI and opens the browser."""

from rushes import runtime

runtime.setup(windowed=True)

from rushes.studio.app import main  # noqa: E402

raise SystemExit(main())
