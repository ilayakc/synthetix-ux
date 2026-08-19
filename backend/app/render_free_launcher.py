"""Supervisor for the zero-cost Render demo container (API + worker + nginx).

Render's free plan has no background-worker instance type, so the FastAPI API
(uvicorn), the ARQ simulation worker and nginx (static frontend) share ONE web
container. `deploy/render_free_start.py` is the entry-point shim; the real,
unit-testable logic lives here so the backend test suite can import it
(bkz. tests/test_render_free_launcher.py).

Guarantees:

- **Fail-closed:** if ANY child exits unexpectedly the whole container exits
  non-zero so Render restarts it - it never leaves the API up while the worker
  is silently dead (that would leave simulations stuck in RUNNING forever).
- **Graceful shutdown:** SIGTERM/SIGINT are forwarded to every child and each is
  given a bounded window to exit before being killed.
- **Exactly one worker:** a single `arq app.worker.WorkerSettings` process is
  started. Render free is a single instance and the engine's DB locking
  (`SELECT ... FOR UPDATE SKIP LOCKED` + advisory locks) keeps processing
  idempotent regardless (bkz. app.services.simulation_worker).
- **Distinguishable logs:** each Python child gets `SYNTHETIX_PROCESS_ROLE`
  (`api` / `worker`) which the structured logger stamps as a `role` field
  (bkz. app.logging_config).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from typing import Protocol


class _Process(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


# Alt sürece stamp'lenecek rol (yalnizca Python süreçleri; nginx Python
# logger'i kullanmaz). Bkz. app.logging_config._PROCESS_ROLE.
_SERVICE_ROLES: dict[str, str] = {"backend": "api", "worker": "worker", "analyzer": "analyzer"}

# Analyzer, backend ile AYNI `app` paket adini kullanir; cakismayi onlemek icin
# ayri bir cwd'den (bkz. Dockerfile.render-free `/opt/analyzer`) baslatilir -
# uvicorn `app.main:app`'i o dizindeki (ANALYZER) pakete gore cozer. Diger
# servisler container WORKDIR'ini (backend paket koku) miras alir.
_SERVICE_CWD: dict[str, str] = {"analyzer": "/opt/analyzer"}

# In-container analyzer'in dinledigi loopback adresi. render.yaml `ANALYZER_
# BASE_URL=http://127.0.0.1:8100` verir; burada da ayni portu dinletiriz.
_ANALYZER_HOST = "127.0.0.1"
_ANALYZER_PORT = "8100"

SpawnFn = Callable[[str, list[str]], _Process]


def build_service_commands() -> dict[str, list[str]]:
    """Tek container'da çalisacak sabit servis komutlari (isim -> argv).

    `worker` girdisi ARQ simulation worker'idir - Render ücretsiz planinda ayri
    bir Background Worker olmadigi icin kuyruk tüketicisi BURADADIR. `analyzer`
    ise Playwright tabanli sayfa analiz servisidir; SADECE loopback'te dinler
    (public edge YOK - uretimde gorulen 429 kaskadinin kok nedeni buydu) ve
    backend/worker ona `http://127.0.0.1:8100` uzerinden ulasir."""

    return {
        "backend": ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
        "worker": ["arq", "app.worker.WorkerSettings"],
        "analyzer": [
            "uvicorn",
            "app.main:app",
            "--host",
            _ANALYZER_HOST,
            "--port",
            _ANALYZER_PORT,
        ],
        "frontend": ["nginx", "-g", "daemon off;"],
    }


def _child_env(service_name: str) -> dict[str, str]:
    env = dict(os.environ)
    role = _SERVICE_ROLES.get(service_name)
    if role:
        env["SYNTHETIX_PROCESS_ROLE"] = role
    return env


def _default_spawn(name: str, command: list[str]) -> _Process:
    # cwd=None -> container WORKDIR'ini miras alir (backend/worker/frontend);
    # analyzer icin /opt/analyzer (ayri `app` paketi - bkz. _SERVICE_CWD).
    return subprocess.Popen(  # noqa: S603 - fixed constant commands
        command, env=_child_env(name), cwd=_SERVICE_CWD.get(name)
    )


def _out(message: str) -> None:
    print(message, flush=True)


def _err(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class Supervisor:
    """Sabit bir servis kümesini baslatir, izler ve birlikte kapatir."""

    def __init__(
        self,
        commands: Mapping[str, list[str]],
        *,
        spawn: SpawnFn = _default_spawn,
        shutdown_timeout: float = 20.0,
    ) -> None:
        self._commands = dict(commands)
        self._spawn = spawn
        self._shutdown_timeout = shutdown_timeout
        self._children: dict[str, _Process] = {}
        self._stopping = False

    def start(self) -> None:
        for name, command in self._commands.items():
            self._children[name] = self._spawn(name, command)
            _out(f"render-free: {name} baslatildi (role={_SERVICE_ROLES.get(name, '-')})")

    def poll_once(self) -> tuple[str, int] | None:
        """Sonlanmis ilk çocugu (isim, çikis kodu) döndürür; yoksa None."""

        for name, process in self._children.items():
            code = process.poll()
            if code is not None:
                return name, code
        return None

    def stop(self, reason: str) -> None:
        """Tüm çalisan çocuklara SIGTERM gönderir (idempotent)."""

        if self._stopping:
            return
        self._stopping = True
        _out(f"render-free: {reason}; servisler kapatiliyor")
        for process in self._children.values():
            if process.poll() is None:
                process.terminate()

    def wait_all(self) -> None:
        """Çocuklarin çikisini sinirli süre bekler; süre dolarsa öldürür."""

        deadline = time.monotonic() + self._shutdown_timeout
        for process in self._children.values():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    @property
    def stopping(self) -> bool:
        return self._stopping

    @property
    def children(self) -> dict[str, _Process]:
        return self._children

    def run(self, *, poll_interval: float = 0.5, install_signal_handlers: bool = True) -> int:
        """Servisleri baslatir ve biri ölene / sinyal gelene kadar izler.

        Bir çocuk beklenmedik biçimde ölürse çikis kodu non-zero olur (temiz
        çikis 0 bile 1'e yükseltilir - uzun ömürlü bir servis 'beklenmedik'
        sonlanmistir) -> container non-zero exit -> Render yeniden baslatir."""

        self.start()

        if install_signal_handlers:

            def _handle(signum: int, _frame: object) -> None:
                self.stop(f"signal={signum}")

            signal.signal(signal.SIGTERM, _handle)
            signal.signal(signal.SIGINT, _handle)

        exit_code = 0
        while not self._stopping:
            dead = self.poll_once()
            if dead is not None:
                name, code = dead
                _err(f"render-free: {name} beklenmedik sekilde kapandi (code={code})")
                exit_code = code or 1
                self.stop(f"{name} kapandi")
                break
            time.sleep(poll_interval)

        self.wait_all()
        return exit_code


def _configure_render_origin() -> None:
    hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if not hostname:
        raise RuntimeError("RENDER_EXTERNAL_HOSTNAME bulunamadi")

    # Frontend and API are served by the same nginx origin. Deriving these
    # values at runtime avoids hard-coding a generated onrender.com hostname.
    os.environ.setdefault("ALLOWED_HOSTS", hostname)
    os.environ.setdefault("CORS_ALLOWED_ORIGIN", f"https://{hostname}")


def _run_migrations() -> None:
    _out("render-free: veritabani migration'i calistiriliyor")
    subprocess.run(["alembic", "upgrade", "head"], check=True)  # noqa: S603, S607


def _bootstrap_admin() -> None:
    _out("render-free: sunum yoneticisi hazirlaniyor")
    subprocess.run([sys.executable, "-m", "app.bootstrap_admin"], check=True)  # noqa: S603


def _bootstrap_user() -> None:
    _out("render-free: sunum kullanicisi hazirlaniyor")
    subprocess.run([sys.executable, "-m", "app.bootstrap_user"], check=True)  # noqa: S603


def _bootstrap_public_demo() -> None:
    # Herkese acik "Canli demo" hesabi yalnizca ortam degiskeni verildiginde
    # hazirlanir; verilmeyen kurulumlari kirmamak icin kosula baglidir.
    if not os.environ.get("DEMO_ACCOUNT_EMAIL"):
        return
    _out("render-free: canli demo hesabi hazirlaniyor")
    subprocess.run([sys.executable, "-m", "app.bootstrap_public_demo"], check=True)  # noqa: S603


def main() -> int:
    _configure_render_origin()
    _run_migrations()
    _bootstrap_admin()
    _bootstrap_user()
    _bootstrap_public_demo()

    _out(
        "render-free: API + ARQ worker + analyzer (127.0.0.1:8100) + nginx tek "
        "container'da baslatiliyor"
    )
    # Not: worker, analyzer henuz hazir degilken bir isi tuketirse analyzer'a
    # yapilan cagri baglanti hatasi verir; bu KALICI, gecikmeli yeniden deneme
    # olarak ele alinir (analyzer_unavailable -> next_attempt_at, varsayilan 15s;
    # bkz. app.services.page_analysis) - yani readiness, bloke etmeyen kontrollu
    # bir backoff ile saglanir; is asla sessizce kaybolmaz.
    return Supervisor(build_service_commands()).run()


if __name__ == "__main__":
    raise SystemExit(main())
