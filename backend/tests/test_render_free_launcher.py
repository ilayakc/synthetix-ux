"""Render ucretsiz (tek container'da API + ARQ worker + nginx) launcher testleri.

`app.render_free_launcher.Supervisor` gercek surec baslatmadan (enjekte edilen
sahte surec nesneleriyle) test edilir: worker dahil tum servisler baslatilir,
bir cocuk beklenmedik biçimde olunce container non-zero cikar (Render yeniden
baslatir - "API acik, worker sessizce olu" durumu olusmaz) ve SIGTERM/graceful
kapatma calisir.
"""

from __future__ import annotations

import signal
import subprocess

import pytest

from app import render_free_launcher
from app.render_free_launcher import (
    _SERVICE_CWD,
    Supervisor,
    _child_env,
    _default_spawn,
    _run_preflight_step,
    _verify_public_port,
    build_service_commands,
    main,
)


class FakeProcess:
    """subprocess.Popen benzeri minimal test cifti."""

    def __init__(self, *, die_on_poll: int | None = None, exit_code: int = 0, hang_on_wait: bool = False):
        self._poll_count = 0
        self._die_on_poll = die_on_poll
        self._exit_code = exit_code
        self._hang_on_wait = hang_on_wait
        self._returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        if (
            self._returncode is None
            and self._die_on_poll is not None
            and self._poll_count >= self._die_on_poll
        ):
            self._returncode = self._exit_code
        self._poll_count += 1
        return self._returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        if not self._hang_on_wait and self._returncode is None:
            self._returncode = -15  # SIGTERM'e temiz cikis taklidi

    def kill(self) -> None:
        self.kill_calls += 1
        self._returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self._returncode is not None:
            return self._returncode
        raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)


def test_build_service_commands_includes_api_worker_analyzer_and_nginx():
    cmds = build_service_commands()
    assert cmds["backend"][0] == "uvicorn"
    assert cmds["worker"] == ["arq", "app.worker.WorkerSettings"]  # kuyruk tuketicisi
    assert cmds["frontend"][0] == "nginx"
    # Analyzer SADECE loopback'te dinler (public edge/429 YOK) - port 8100.
    assert cmds["analyzer"][0] == "uvicorn"
    assert "app.main:app" in cmds["analyzer"]
    assert "127.0.0.1" in cmds["analyzer"]
    assert "8100" in cmds["analyzer"]


def test_analyzer_runs_from_isolated_working_directory():
    # Backend ile ayni `app` paket adi cakismasini onlemek icin analyzer ayri
    # bir cwd'den baslatilir; diger servisler WORKDIR'i (cwd=None) miras alir.
    assert _SERVICE_CWD["analyzer"] == "/opt/analyzer"
    assert "backend" not in _SERVICE_CWD
    assert "worker" not in _SERVICE_CWD


def test_default_spawn_passes_isolated_cwd_for_analyzer(monkeypatch):
    calls: dict[str, object] = {}

    def fake_popen(command, *, env=None, cwd=None):  # noqa: ANN001
        calls["command"] = command
        calls["cwd"] = cwd
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    _default_spawn("analyzer", ["uvicorn", "app.main:app"])
    assert calls["cwd"] == "/opt/analyzer"

    calls.clear()
    _default_spawn("backend", ["uvicorn", "app.main:app"])
    assert calls["cwd"] is None  # WORKDIR miras alinir


def test_child_env_labels_python_services_only(monkeypatch):
    monkeypatch.delenv("SYNTHETIX_PROCESS_ROLE", raising=False)
    assert _child_env("backend")["SYNTHETIX_PROCESS_ROLE"] == "api"
    assert _child_env("worker")["SYNTHETIX_PROCESS_ROLE"] == "worker"
    assert _child_env("analyzer")["SYNTHETIX_PROCESS_ROLE"] == "analyzer"
    assert "SYNTHETIX_PROCESS_ROLE" not in _child_env("frontend")


def test_start_spawns_api_worker_analyzer_and_nginx():
    spawned: dict[str, list[str]] = {}

    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        spawned[name] = command
        return FakeProcess()

    Supervisor(build_service_commands(), spawn=fake_spawn).start()

    assert set(spawned) == {"backend", "worker", "analyzer", "frontend"}
    assert spawned["worker"] == ["arq", "app.worker.WorkerSettings"]


def test_run_fails_container_when_worker_dies():
    procs: dict[str, FakeProcess] = {}

    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        proc = FakeProcess(die_on_poll=0 if name == "worker" else None, exit_code=3)
        procs[name] = proc
        return proc

    supervisor = Supervisor(build_service_commands(), spawn=fake_spawn, shutdown_timeout=0.1)
    code = supervisor.run(poll_interval=0, install_signal_handlers=False)

    assert code == 3  # worker'in non-zero kodu propagate olur
    assert procs["backend"].terminate_calls == 1  # digerleri kapatildi
    assert procs["frontend"].terminate_calls == 1
    assert procs["worker"].terminate_calls == 0  # zaten olu


def test_run_fails_container_when_analyzer_dies():
    # Analyzer beklenmedik sekilde kapanirsa container fail-closed olmali (Render
    # yeniden baslatir) - aksi halde API acik kalir ama sayfa analizi sessizce
    # calismaz.
    procs: dict[str, FakeProcess] = {}

    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        proc = FakeProcess(die_on_poll=0 if name == "analyzer" else None, exit_code=7)
        procs[name] = proc
        return proc

    supervisor = Supervisor(build_service_commands(), spawn=fake_spawn, shutdown_timeout=0.1)
    code = supervisor.run(poll_interval=0, install_signal_handlers=False)

    assert code == 7
    assert procs["backend"].terminate_calls == 1
    assert procs["worker"].terminate_calls == 1
    assert procs["frontend"].terminate_calls == 1
    assert procs["analyzer"].terminate_calls == 0  # zaten olu


def test_run_fails_container_even_on_clean_child_exit():
    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        return FakeProcess(die_on_poll=0 if name == "worker" else None, exit_code=0)

    supervisor = Supervisor(build_service_commands(), spawn=fake_spawn, shutdown_timeout=0.1)
    code = supervisor.run(poll_interval=0, install_signal_handlers=False)

    # Uzun omurlu bir servisin "temiz" (0) cikisi bile beklenmedik sayilir -> 1.
    assert code == 1


def test_stop_terminates_running_children_and_is_idempotent():
    procs: list[FakeProcess] = []

    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        proc = FakeProcess()
        procs.append(proc)
        return proc

    supervisor = Supervisor(build_service_commands(), spawn=fake_spawn)
    supervisor.start()
    supervisor.stop("test signal")
    supervisor.stop("tekrar")  # idempotent - ikinci kez terminate cagirmaz

    assert supervisor.stopping is True
    for proc in procs:
        assert proc.terminate_calls == 1


def test_wait_all_kills_children_that_ignore_terminate():
    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        return FakeProcess(hang_on_wait=True)

    supervisor = Supervisor(build_service_commands(), spawn=fake_spawn, shutdown_timeout=0.0)
    supervisor.start()
    supervisor.stop("test")
    supervisor.wait_all()

    for proc in supervisor.children.values():
        assert proc.kill_calls == 1


def test_start_skips_already_started_service():
    # Onceden spawn_service ile baslatilan nginx `start()` tarafindan TEKRAR
    # baslatilmaz (nginx iki kez baslatilmaz).
    counts: dict[str, int] = {}

    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        counts[name] = counts.get(name, 0) + 1
        return FakeProcess()

    supervisor = Supervisor(build_service_commands(), spawn=fake_spawn)
    supervisor.spawn_service("frontend")
    supervisor.start()

    assert counts["frontend"] == 1  # yalnizca bir kez
    assert set(supervisor.children) == {"backend", "worker", "analyzer", "frontend"}


def test_spawn_service_rejects_double_start():
    supervisor = Supervisor(build_service_commands(), spawn=lambda name, command: FakeProcess())
    supervisor.spawn_service("frontend")
    with pytest.raises(RuntimeError):
        supervisor.spawn_service("frontend")


# --- PORT dogrulama --------------------------------------------------------


def test_verify_public_port_accepts_10000(monkeypatch):
    monkeypatch.setenv("PORT", "10000")
    _verify_public_port()  # firlatmamali


def test_verify_public_port_allows_unset(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    _verify_public_port()  # firlatmamali (ör. yerel calistirma)


def test_verify_public_port_rejects_mismatch(monkeypatch):
    monkeypatch.setenv("PORT", "8080")
    with pytest.raises(RuntimeError) as exc:
        _verify_public_port()
    assert "10000" in str(exc.value)


# --- On hazirlik (preflight) adimlari --------------------------------------


def test_preflight_step_raises_clear_error_on_failure(monkeypatch):
    def fake_run(argv, *, check=False, timeout=None):
        raise subprocess.CalledProcessError(returncode=5, cmd=argv)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as exc:
        _run_preflight_step("veritabani migration'i", ["alembic", "upgrade", "head"])
    assert "migration" in str(exc.value)
    assert "5" in str(exc.value)


def test_preflight_step_raises_on_timeout(monkeypatch):
    def fake_run(argv, *, check=False, timeout=None):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as exc:
        _run_preflight_step("veritabani migration'i", ["alembic", "upgrade", "head"])
    assert "zaman asimina" in str(exc.value)


# --- main(): port ONCE acilir, sonra on hazirlik --------------------------


@pytest.fixture
def _stub_preflight_env(monkeypatch):
    # main() testlerini Render ortam degiskenlerinden yalitir.
    monkeypatch.setattr(render_free_launcher, "_configure_render_origin", lambda: None)
    monkeypatch.setattr(render_free_launcher, "_verify_public_port", lambda: None)


def test_main_opens_public_port_before_preflight(_stub_preflight_env):
    calls: list[str] = []

    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        calls.append(f"spawn:{name}")
        # backend ilk poll'da olsun ki run() hemen donsun (sonsuz izleme yok).
        return FakeProcess(die_on_poll=0 if name == "backend" else None, exit_code=1)

    supervisor = Supervisor(build_service_commands(), spawn=fake_spawn, shutdown_timeout=0.1)

    def fake_preflight() -> None:
        calls.append("preflight")

    main(supervisor=supervisor, run_preflight=fake_preflight, install_signal_handlers=False)

    # nginx (frontend) on hazirliktan ONCE baslar.
    assert calls.index("spawn:frontend") < calls.index("preflight")
    # backend/worker/analyzer on hazirliktan SONRA baslar.
    for service in ("backend", "worker", "analyzer"):
        assert calls.index("preflight") < calls.index(f"spawn:{service}")


def test_main_starts_nginx_exactly_once(_stub_preflight_env):
    counts: dict[str, int] = {}

    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        counts[name] = counts.get(name, 0) + 1
        return FakeProcess(die_on_poll=0 if name == "backend" else None, exit_code=1)

    supervisor = Supervisor(build_service_commands(), spawn=fake_spawn, shutdown_timeout=0.1)
    main(supervisor=supervisor, run_preflight=lambda: None, install_signal_handlers=False)

    assert counts["frontend"] == 1


def test_main_success_monitors_four_services(_stub_preflight_env):
    procs: dict[str, FakeProcess] = {}

    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        proc = FakeProcess(die_on_poll=0 if name == "backend" else None, exit_code=2)
        procs[name] = proc
        return proc

    supervisor = Supervisor(build_service_commands(), spawn=fake_spawn, shutdown_timeout=0.1)
    code = main(supervisor=supervisor, run_preflight=lambda: None, install_signal_handlers=False)

    # Dort servis de baslatilip izlenir; backend olunce fail-closed (code propagate).
    assert set(supervisor.children) == {"backend", "worker", "analyzer", "frontend"}
    assert code == 2
    assert procs["frontend"].terminate_calls == 1  # nginx da supervisor'ca kapatildi


def test_main_shuts_down_nginx_when_preflight_fails(_stub_preflight_env):
    procs: dict[str, FakeProcess] = {}

    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        proc = FakeProcess()
        procs[name] = proc
        return proc

    supervisor = Supervisor(build_service_commands(), spawn=fake_spawn, shutdown_timeout=0.1)

    def failing_preflight() -> None:
        raise RuntimeError("migration patladi")

    code = main(
        supervisor=supervisor, run_preflight=failing_preflight, install_signal_handlers=False
    )

    assert code == 1
    # Yalnizca nginx baslatilmisti ve kapatildi; kalan servisler hic baslatilmadi.
    assert set(procs) == {"frontend"}
    assert procs["frontend"].terminate_calls == 1


def test_signal_terminates_all_children():
    # signal=15 (SIGTERM) tum cocuklari kapatir. Gercek sinyal gondermek yerine
    # kayitli handler dogrudan cagirilir (Windows/pytest'te tasinabilir).
    procs: list[FakeProcess] = []

    def fake_spawn(name: str, command: list[str]) -> FakeProcess:
        proc = FakeProcess()
        procs.append(proc)
        return proc

    supervisor = Supervisor(build_service_commands(), spawn=fake_spawn)
    supervisor.start()

    original = signal.getsignal(signal.SIGTERM)
    try:
        supervisor.install_signal_handlers()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)  # signal=15
    finally:
        signal.signal(signal.SIGTERM, original)

    assert supervisor.stopping is True
    for proc in procs:
        assert proc.terminate_calls == 1
