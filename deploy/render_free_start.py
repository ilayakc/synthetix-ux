"""Entry-point shim for the zero-cost Render demo container.

Render runs `python /opt/synthetix/render_free_start.py` (see
Dockerfile.render-free). The real, unit-testable supervisor logic lives in the
backend package at `app.render_free_launcher` so the backend test suite can
import it directly. This file only ensures the backend working directory (the
image WORKDIR, `/app`) is importable and then delegates to `main()`.
"""

from __future__ import annotations

import os
import sys

# The script runs from /opt/synthetix, so /app is not on sys.path by default;
# the image WORKDIR is /app (the backend package root). Add the current working
# directory so `import app.render_free_launcher` resolves.
sys.path.insert(0, os.getcwd())

from app.render_free_launcher import main  # noqa: E402 - must follow the sys.path fix above

if __name__ == "__main__":
    raise SystemExit(main())
