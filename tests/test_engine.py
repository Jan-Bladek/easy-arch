from pathlib import Path

import pytest

from arch_gui_installer.core.config import (
    Config,
    DeviceIdentity,
    Secrets,
    ValidationError,
)
from arch_gui_installer.installation.engine import Engine
from arch_gui_installer.installation.runner import CommandError, Runner
from arch_gui_installer.storage.plan import make_plan


def engine(tmp_path):
    c = Config(DeviceIdentity("/dev/vdz", "254:90", 32 * 1024**3))
    e = Engine(
        make_plan(c, ["base"], {}), Secrets("test-password"), Runner(lambda e: None)
    )
    e.target = tmp_path / "target"
    e.target.mkdir()
    return e


def test_unapproved_plan_never_executes(tmp_path):
    e = engine(tmp_path)
    with pytest.raises(ValidationError, match="Approval"):
        e.install("wrong")
    assert list(e.target.iterdir()) == []


def test_write_rejects_symlink_and_escape(tmp_path):
    e = engine(tmp_path)
    (e.target / "etc").symlink_to(tmp_path)
    with pytest.raises(RuntimeError):
        e.write("etc/passwd", "bad")
    with pytest.raises(ValueError):
        e.write("../bad", "bad")
    with pytest.raises(ValueError):
        e.write("/bad", "bad")


def test_failed_stage_is_reported(tmp_path):
    e = engine(tmp_path)
    events = []
    e.r.emit_callback = events.append

    def fail():
        raise CommandError("network unavailable")

    with pytest.raises(CommandError):
        e.stage(1, "Network", fail)
    assert [x["status"] for x in events if x["event"] == "stage"] == [
        "running",
        "failed",
    ]


def test_cleanup_reports_failure(tmp_path):
    e = engine(tmp_path)
    events = []
    e.r.emit_callback = events.append
    e.mounted = True

    def fail(*args, **kw):
        raise CommandError("busy")

    e.r.run = fail
    e.cleanup()
    assert any(x["event"] == "cleanup_error" for x in events)
    assert e.mounted


def test_real_host_rejected_before_storage(tmp_path, monkeypatch):
    import arch_gui_installer.installation.engine as module

    e = engine(tmp_path)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(Path, "is_file", lambda p: False)
    monkeypatch.setattr(Path, "is_mount", lambda p: False)
    with pytest.raises(RuntimeError, match="ordinary host"):
        e.preflight()


def test_network_failure_stops_before_storage(tmp_path, monkeypatch):
    e = engine(tmp_path)
    called = []

    def fail():
        raise CommandError("network unavailable")

    e.preflight = fail
    e.storage = lambda: called.append("write")
    with pytest.raises(CommandError):
        e.install(e.plan.digest())
    assert called == []


def test_transaction_failure_stops_before_storage(tmp_path):
    e = engine(tmp_path)
    called = []
    e.preflight = lambda: None

    def fail():
        raise CommandError("package unavailable or conflict")

    e.packages = fail
    e.storage = lambda: called.append("write")
    with pytest.raises(CommandError):
        e.install(e.plan.digest())
    assert called == []


def test_out_of_space_fails_mount_stage_and_unmounts(tmp_path, monkeypatch):
    from collections import namedtuple

    import arch_gui_installer.installation.engine as module

    e = engine(tmp_path)
    e.target.mkdir(exist_ok=True)
    calls = []
    e.r.run = lambda args, **kw: calls.append(args) or ""
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: usage(100, 99, 1))
    with pytest.raises(RuntimeError, match="12 GiB"):
        e.mount()
    e.cleanup()
    assert any(c[0] == "umount" for c in calls)


def test_preserved_user_config_gets_backup(tmp_path):
    from arch_gui_installer.core.config import Partition

    e = engine(tmp_path)
    e.parts.append(Partition(3, 40000, 1000, "ext4", "/home", "preserve"))
    original = e.target / "home/arch/.config/test.conf"
    original.parent.mkdir(parents=True)
    original.write_text("keep my data")
    e.write("home/arch/.config/test.conf", "new config")
    assert original.read_text() == "new config"
    backups = list(original.parent.glob("test.conf.easy-arch-backup-*"))
    assert len(backups) == 1 and backups[0].read_text() == "keep my data"


def test_config_write_does_not_truncate_hardlinks(tmp_path):
    import os

    e = engine(tmp_path)
    original = e.target / "original"
    original.write_text("untouched")
    os.link(original, e.target / "config")
    e.write("config", "replacement")
    assert original.read_text() == "untouched"
