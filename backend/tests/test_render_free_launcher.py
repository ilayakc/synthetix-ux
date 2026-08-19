"""Render ucretsiz (tek container'da API + ARQ worker + nginx) launcher testleri.

`app.render_free_launcher.Supervisor` gercek surec baslatmadan (enjekte edilen
sahte surec nesneleriyle) test edilir: worker dahil tum servisler baslatilir,
bir cocuk beklenmedik biçimde olunce container non-zero cikar (Render yeniden
baslatir - "API acik, worker sessizce olu" durumu olusmaz) ve SIGTERM/graceful
kapatma calisir.
"""

from __future__ import annotations

import subprocess

from app.render_free_launcher import (
    _SERVICE_CWD,
    Supervisor,
    _child_env,
    _default_spawn,
    build_service_commands,
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
