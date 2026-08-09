"""Start the zero-cost Render demo processes in a single web instance.

Render's free plan has no background-worker instance type. For the demo only,
nginx, FastAPI and ARQ therefore share one container. Any child exiting stops
the container so Render can restart it instead of leaving a partly working app.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def _configure_render_origin() -> None:
    hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if not hostname:
        raise RuntimeError("RENDER_EXTERNAL_HOSTNAME bulunamadi")

    # Frontend and API are served by the same nginx origin. Deriving these
    # values at runtime avoids hard-coding a generated onrender.com hostname.
    os.environ.setdefault("ALLOWED_HOSTS", hostname)
    os.environ.setdefault("CORS_ALLOWED_ORIGIN", f"https://{hostname}")


def _run_migrations() -> None:
    print("render-free: veritabani migration'i calistiriliyor", flush=True)
    subprocess.run(["alembic", "upgrade", "head"], check=True)


def main() -> int:
    _configure_render_origin()
    _run_migrations()

    commands = {
        "backend": ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
        "worker": ["arq", "app.worker.WorkerSettings"],
        "frontend": ["nginx", "-g", "daemon off;"],
    }
    children = {
        name: subprocess.Popen(command)  # noqa: S603 - commands are fixed constants
        for name, command in commands.items()
    }
    stopping = False

    def stop_children(signum: int, _frame: object) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        print(f"render-free: signal={signum}; servisler kapatiliyor", flush=True)
        for process in children.values():
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)

    exit_code = 0
    while not stopping:
        for name, process in children.items():
            return_code = process.poll()
            if return_code is not None:
                print(
                    f"render-free: {name} beklenmedik sekilde kapandi (code={return_code})",
                    file=sys.stderr,
                    flush=True,
                )
                exit_code = return_code or 1
                stop_children(signal.SIGTERM, None)
                break
        time.sleep(0.5)

    deadline = time.monotonic() + 20
    for process in children.values():
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
