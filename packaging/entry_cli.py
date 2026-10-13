"""Entry point for the bundled command-line tool (rushes.exe)."""

from rushes import runtime

runtime.setup()

from rushes.cli import main  # noqa: E402

raise SystemExit(main())
