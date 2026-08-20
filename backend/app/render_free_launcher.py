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

    def spawn_service(self, name: str) -> _Process:
        """Tek bir servisi baslatir ve izlemeye alir.

        nginx gibi bir servisi on hazirliktan (migration/bootstrap) ONCE baslatip
        public portu hemen acmak icin kullanilir; ardindan `start()` KALAN
        servisleri baslatirken bunu tekrar baslatmaz - yani nginx IKI KEZ
        baslatilmaz (bkz. `main`)."""

        if name in self._children:
            raise RuntimeError(f"{name} zaten baslatildi")
        process = self._spawn(name, self._commands[name])
        self._children[name] = process
        _out(f"render-free: {name} baslatildi (role={_SERVICE_ROLES.get(name, '-')})")
        return process

    def start(self) -> None:
        """Henuz baslatilmamis tum servisleri baslatir.

        Onceden `spawn_service` ile baslatilan (ör. nginx) servisler atlanir;
        kapanis basladiysa (stopping) hicbir yeni servis baslatilmaz."""

        if self._stopping:
            return
        for name in self._commands:
            if name not in self._children:
                self.spawn_service(name)

    def install_signal_handlers(self) -> None:
        """SIGTERM/SIGINT'i graceful shutdown'a baglar (idempotent).

        `run`'dan ayri cagirilabilir; boylece nginx portu acildiktan sonra ama
        on hazirlik SIRASINDA gelen bir sinyal de tum cocuklari (nginx dahil)
        kontrollu bicimde kapatir."""

        def _handle(signum: int, _frame: object) -> None:
            self.stop(f"signal={signum}")

        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)

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

        if install_signal_handlers:
            self.install_signal_handlers()

        self.start()

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


# nginx'in dinledigi SABIT public port. `deploy/nginx.render-free.conf` icinde
# `listen 10000` ile sabittir ve Render varsayilan PORT'u da 10000'dir
# (Dockerfile.render-free `ENV PORT=10000`).
_PUBLIC_PORT = "10000"

# On hazirlik (migration + bootstrap) adimlari icin ust sinir. Birincil duzeltme
# bu adimlari nginx portu ACILDIKTAN sonra calistirmaktir; dolayisiyla bu timeout
# yalnizca GERCEK bir takilmaya (ör. yanit vermeyen bir DB baglantisi) karsi
# emniyet subabidir ve genis tutulur. Alembic her migration'i tek bir transaction
# icinde calistirir; sure dolup surec oldurulurse Postgres devam eden
# transaction'i geri alir - yani YARIM uygulanmis migration olusmaz (bu noktaya
# kadar commit'lenmis migration'lar zaten TAMDIR). Ortamdan (saniye) gecersiz
# kilinabilir.
_PREFLIGHT_STEP_TIMEOUT_SECONDS = float(
    os.environ.get("RENDER_FREE_PREFLIGHT_TIMEOUT_SECONDS", "600")
)


def _verify_public_port() -> None:
    """Render'in atadigi PORT'un nginx'in dinledigi sabit port ile eslestigini
    dogrular.

    nginx yapilandirmasi `listen 10000` ile sabittir; Render farkli bir PORT
    atarsa nginx o portu dinlemez ve port taramasi yine basarisiz olur. Bu
    durumda sessizce yanlis calismaktansa acik bir hatayla erken durulur. PORT
    tanimsizsa (ör. yerel calistirma) dogrulama atlanir."""

    port = (os.environ.get("PORT") or "").strip()
    if port and port != _PUBLIC_PORT:
        raise RuntimeError(
            f"PORT={port} fakat nginx sabit olarak {_PUBLIC_PORT} portunu dinliyor; "
            f"deploy PORT={_PUBLIC_PORT} kullanmali (bkz. deploy/nginx.render-free.conf)"
        )


def _run_preflight_step(label: str, argv: list[str]) -> None:
    """Bir on hazirlik alt surecini calistirir; baslangic/bitis sureleriyle
    loglar ve makul bir timeout uygular.

    Sadece sabit `label` ve gecen sure loglanir - komut argv'i veya cikti
    loglanmaz (gizli veri sizmaz). Hata/timeout durumunda anlasilir bir
    `RuntimeError` firlatir; cagiran taraf (bkz. `main`) fail-closed kapanir."""

    _out(f"render-free: {label} basladi")
    started = time.monotonic()
    try:
        subprocess.run(argv, check=True, timeout=_PREFLIGHT_STEP_TIMEOUT_SECONDS)  # noqa: S603
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        raise RuntimeError(
            f"{label} {elapsed:.1f}s sonra zaman asimina ugradi "
            f"(sinir={_PREFLIGHT_STEP_TIMEOUT_SECONDS:.0f}s)"
        ) from exc
    except subprocess.CalledProcessError as exc:
        elapsed = time.monotonic() - started
        raise RuntimeError(
            f"{label} {elapsed:.1f}s sonra basarisiz oldu (code={exc.returncode})"
        ) from exc
    _out(f"render-free: {label} tamamlandi ({time.monotonic() - started:.1f}s)")


def _run_migrations() -> None:
    _run_preflight_step("veritabani migration'i", ["alembic", "upgrade", "head"])


def _bootstrap_admin() -> None:
    _run_preflight_step("sunum yoneticisi bootstrap'i", [sys.executable, "-m", "app.bootstrap_admin"])


def _bootstrap_user() -> None:
    _run_preflight_step("sunum kullanicisi bootstrap'i", [sys.executable, "-m", "app.bootstrap_user"])


def _bootstrap_public_demo() -> None:
    # Herkese acik "Canli demo" hesabi yalnizca ortam degiskeni verildiginde
    # hazirlanir; verilmeyen kurulumlari kirmamak icin kosula baglidir.
    if not os.environ.get("DEMO_ACCOUNT_EMAIL"):
        return
    _run_preflight_step(
        "canli demo hesabi bootstrap'i", [sys.executable, "-m", "app.bootstrap_public_demo"]
    )


def _run_preflight() -> None:
    """Migration + bootstrap adimlarini SIRAYLA calistirir.

    Bu adimlar public nginx portu ACILDIKTAN sonra calisir; boylece cold-start
    Postgres yavaslamasinda Render'in port taramasi zaman asimina ugramaz
    (gozlemlenen "Port scan timeout" kok nedeni)."""

    _run_migrations()
    _bootstrap_admin()
    _bootstrap_user()
    _bootstrap_public_demo()


def main(
    *,
    supervisor: Supervisor | None = None,
    run_preflight: Callable[[], None] = _run_preflight,
    install_signal_handlers: bool = True,
) -> int:
    _configure_render_origin()
    _verify_public_port()

    if supervisor is None:
        supervisor = Supervisor(build_service_commands())

    # Sinyal kurulumunu nginx acilmadan ONCE yap: on hazirlik SIRASINDA gelen bir
    # SIGTERM de tum cocuklari (nginx dahil) kontrollu kapatir.
    if install_signal_handlers:
        supervisor.install_signal_handlers()

    # 1. Public nginx listener'i migration/bootstrap'tan ONCE baslat: 0.0.0.0:10000
    #    hemen acilir. Boylece cold-start on hazirligi uzasa bile Render'in port
    #    taramasi zaman asimina ugramaz. Backend henuz baslamadigi icin /api/health
    #    nginx uzerinden 502 doner - YANLIS bir 'healthy' yanit URETILMEZ; yalnizca
    #    TCP portu erisilebilir olur.
    supervisor.spawn_service("frontend")

    # 2. On hazirlik (migration + bootstrap) port ACIKken calisir. Herhangi biri
    #    basarisiz olursa nginx dahil baslatilmis tum surecler kapatilir ve
    #    container non-zero cikar (Render yeniden baslatir). Yarim migration
    #    olusmaz (bkz. _PREFLIGHT_STEP_TIMEOUT_SECONDS).
    try:
        run_preflight()
    except Exception as exc:
        _err(
            f"render-free: on hazirlik basarisiz ({exc}); nginx dahil tum surecler "
            "kapatiliyor"
        )
        supervisor.stop("on hazirlik basarisiz")
        supervisor.wait_all()
        return 1

    if supervisor.stopping:
        # On hazirlik sirasinda SIGTERM/SIGINT geldi: nginx zaten kapatildi,
        # kalan servisleri baslatma.
        supervisor.wait_all()
        return 0

    # 3. Kalan servisleri baslat; supervisor onceden baslatilan nginx'i de birlikte
    #    izler (nginx IKI KEZ baslatilmaz). Not: worker, analyzer henuz hazir
    #    degilken bir isi tuketirse analyzer cagrisi baglanti hatasi verir; bu
    #    KALICI, gecikmeli yeniden deneme olarak ele alinir (analyzer_unavailable ->
    #    next_attempt_at, varsayilan 15s; bkz. app.services.page_analysis) - is asla
    #    sessizce kaybolmaz.
    _out(
        "render-free: on hazirlik tamam; API + ARQ worker + analyzer "
        "(127.0.0.1:8100) baslatiliyor (nginx zaten dinliyor)"
    )
    return supervisor.run(install_signal_handlers=False)


if __name__ == "__main__":
    raise SystemExit(main())
