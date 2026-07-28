"""`compose.prod.yaml`'in kabul edilen production mimarisiyle (bkz.
docs/production.md) uyumlu kaldigini kanitlayan statik yapilandirma
testleri.

Bu testler docker calistirmaz - yalnizca YAML'i parse edip yapisini
dogrular; gercek stack dogrulamasi (`docker compose config`, izole smoke
ortami) ayrica, testler DISINDA yapilir.

ONEMLI (bilinen sinirlama): `compose.prod.yaml` repo KOKUNDE yasar, ama
gelistirme backend container'i KASITLI OLARAK yalnizca `./backend:/app`
bind-mount eder (repo kokunu degil) - bu, dev container'in production compose
dosyasina hicbir sekilde bagimli/farkinda olmamasini saglayan mevcut
izolasyonun bir parcasidir ve degistirilmez. Bu yuzden bu dosya birden fazla
olasi konumu dener; hicbiri bulunamazsa (normal `docker compose exec backend
pytest` calistirmasinda beklenen durum budur) testler ACIKCA SKIP edilir -
sessizce PASS olmaz. Bu testler, repo kokunun erisilebilir oldugu bir ortamda
(ornegin `docker cp compose.prod.yaml <backend-container>:/compose.prod.yaml`
veya repo kokunun mount edildigi bir CI ortaminda) calistirilarak dogrulanir
- bkz. Paket 5B final raporu.
"""

import os
import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit


def _find_compose_prod_path() -> Path | None:
    candidates = [
        Path(__file__).resolve().parents[2] / "compose.prod.yaml",
        Path("/compose.prod.yaml"),
        Path("/repo/compose.prod.yaml"),
    ]
    env_override = os.environ.get("COMPOSE_PROD_PATH")
    if env_override:
        candidates.insert(0, Path(env_override))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


_COMPOSE_PATH = _find_compose_prod_path()


@pytest.fixture(scope="module")
def compose_prod() -> dict:
    if _COMPOSE_PATH is None:
        pytest.skip(
            "compose.prod.yaml bu container'dan erisilemiyor (bkz. bu dosyanin "
            "ust yorumu) - dev backend container'i yalnizca ./backend'i "
            "mount eder. COMPOSE_PROD_PATH env degiskeni veya bilinen bir "
            "konuma dosya kopyalanarak calistirilabilir."
        )
    with _COMPOSE_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_compose_prod_file_exists():
    if _COMPOSE_PATH is None:
        pytest.skip(
            "compose.prod.yaml bu container'dan erisilemiyor (bkz. bu dosyanin ust yorumu)."
        )
    assert _COMPOSE_PATH.is_file()


def test_project_name_is_distinct_from_development_stack(compose_prod):
    # Development (`compose.yaml`) `name: synthetix-ux` kullanir; production
    # stack'in AYNI proje adini kullanmasi, ayni volume/network/container
    # isim uzayini paylasarak development verisini riske atar.
    assert compose_prod.get("name") == "synthetix-ux-prod"


def test_migrate_service_command_is_exactly_alembic_upgrade_head(compose_prod):
    migrate = compose_prod["services"]["migrate"]
    assert migrate["command"] == ["alembic", "upgrade", "head"]
    # One-shot: yeniden baslatma politikasi olmamali (restart dongusune girmemeli).
    assert migrate.get("restart") in (None, "no")


def test_only_migrate_service_runs_migrations(compose_prod):
    for name, service in compose_prod["services"].items():
        if name == "migrate":
            continue
        command = service.get("command")
        if command is None:
            continue
        joined = " ".join(command) if isinstance(command, list) else str(command)
        assert "alembic" not in joined.lower(), f"{name} servisi migration calistirmamali"


@pytest.mark.parametrize("service_name", ["backend", "worker"])
def test_backend_and_worker_wait_for_migrate_completion(compose_prod, service_name):
    depends_on = compose_prod["services"][service_name]["depends_on"]
    assert "migrate" in depends_on
    assert depends_on["migrate"]["condition"] == "service_completed_successfully"


def test_only_frontend_service_publishes_a_host_port(compose_prod):
    services_with_ports = [
        name for name, service in compose_prod["services"].items() if "ports" in service
    ]
    assert services_with_ports == ["frontend"]


@pytest.mark.parametrize("service_name", ["backend", "worker", "analyzer", "db", "redis"])
def test_internal_services_publish_no_ports(compose_prod, service_name):
    assert "ports" not in compose_prod["services"][service_name]


@pytest.mark.parametrize("service_name", ["backend", "worker", "analyzer", "frontend"])
def test_no_source_bind_mounts(compose_prod, service_name):
    """Production'da kod image icine COPY ile gomulur; host kaynak dizini
    bind-mount edilmemelidir (aksi halde container restart'i degistirilmis/
    eski kodla calisabilir)."""

    volumes = compose_prod["services"][service_name].get("volumes", [])
    for volume in volumes:
        entry = volume if isinstance(volume, str) else volume.get("source", "")
        assert not str(entry).startswith("./"), f"{service_name} bind-mount kullanmamali: {entry}"


def test_backend_command_has_no_reload_flag(compose_prod):
    command = compose_prod["services"]["backend"]["command"]
    joined = " ".join(command)
    assert "--reload" not in joined


def test_backend_and_worker_share_same_build_context(compose_prod):
    backend_context = compose_prod["services"]["backend"]["build"]["context"]
    worker_context = compose_prod["services"]["worker"]["build"]["context"]
    migrate_context = compose_prod["services"]["migrate"]["build"]["context"]
    assert backend_context == worker_context == migrate_context == "./backend"


def test_db_and_redis_use_named_volumes(compose_prod):
    db_volumes = compose_prod["services"]["db"]["volumes"]
    redis_volumes = compose_prod["services"]["redis"]["volumes"]
    assert any("pgdata_prod" in v for v in db_volumes)
    assert any("redis_data_prod" in v for v in redis_volumes)
    assert "pgdata_prod" in compose_prod["volumes"]
    assert "redis_data_prod" in compose_prod["volumes"]


def test_images_are_pinned_not_latest(compose_prod):
    for name in ("db", "redis"):
        image = compose_prod["services"][name]["image"]
        assert ":latest" not in image
        assert ":" in image  # bir tag'e pinlenmis olmali


def test_backend_healthcheck_uses_ready_endpoint(compose_prod):
    healthcheck = compose_prod["services"]["backend"]["healthcheck"]["test"]
    joined = " ".join(healthcheck)
    assert "/api/ready" in joined


# --- db-backup (yedekleme) servisi -----------------------------------------


def test_db_backup_service_exists_and_publishes_no_port(compose_prod):
    assert "db-backup" in compose_prod["services"]
    assert "ports" not in compose_prod["services"]["db-backup"]


def test_db_backup_uses_dedicated_named_volume(compose_prod):
    volumes = compose_prod["services"]["db-backup"]["volumes"]
    assert any("pgbackups_prod" in v for v in volumes)
    assert "pgbackups_prod" in compose_prod["volumes"]
    # Yedekler `pgdata_prod` ile AYNI volume'e YAZILMAMALI (aksi halde db
    # verisiyle ayni disk basarisizligina karsi hicbir koruma saglamaz -
    # bkz. docs/production.md "Bilinen sinirlamalar").
    assert not any("pgdata_prod" in v for v in volumes)


def test_db_backup_waits_for_db_healthy(compose_prod):
    depends_on = compose_prod["services"]["db-backup"]["depends_on"]
    assert depends_on["db"]["condition"] == "service_healthy"


def test_db_backup_does_not_run_migrations(compose_prod):
    command = compose_prod["services"]["db-backup"]["command"]
    joined = " ".join(command) if isinstance(command, list) else str(command)
    assert "alembic" not in joined.lower()


def test_db_backup_restarts_unless_stopped(compose_prod):
    assert compose_prod["services"]["db-backup"].get("restart") == "unless-stopped"


def test_db_backup_script_does_not_leak_compose_time_variable_refs(compose_prod):
    # `command:` yaml'dan okundugunda `$$` YAML/compose tarafindan
    # DEGISTIRILMEZ (yalnizca gercek `docker compose` calistirmasi sirasinda
    # `$$` -> `$` interpolasyonu yapilir) - bu yuzden burada script metninde
    # HALA `$$` olarak gormeliyiz. Kacirilmis (tek `$`) bir referans, o
    # degiskenin compose tarafindan yanlislikla (ve container calisma
    # zamaninda BOS bir deger olarak) erkenden cozumlenecegi anlamina gelir.
    command = compose_prod["services"]["db-backup"]["command"]
    script = command[-1] if isinstance(command, list) else str(command)

    leaked = re.findall(r"(?<!\$)\$(?:\{\w+\}|\w+|\()", script)
    assert leaked == [], f"kacirilmamis (compose-time'da cozumlenecek) degisken referansi bulundu: {leaked}"
