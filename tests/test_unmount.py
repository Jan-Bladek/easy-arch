import pytest
from test_engine import engine

import arch_gui_installer.installation.unmount as module
from arch_gui_installer.installation.runner import CommandError, Runner
from arch_gui_installer.installation.unmount import UnmountError, unmount


def test_busy_target_reports_diagnostics_without_force_or_killing(
    tmp_path, monkeypatch
):
    events, commands = [], []
    runner = Runner(events.append)

    def run(args, **kwargs):
        commands.append(args)
        if args[0] == "umount":
            raise CommandError("umount exited with status 32")
        if args[0] == "fuser":
            raise CommandError("no processes visible")

    runner.run = run
    monkeypatch.setattr(module, "which", lambda tool: "/usr/bin/" + tool)
    with pytest.raises(UnmountError, match="Do not repeat partitioning") as caught:
        unmount(runner, tmp_path)
    assert isinstance(caught.value.__cause__, CommandError)
    assert [command[0] for command in commands] == [
        "umount",
        "findmnt",
        "fuser",
        "swapon",
    ]
    assert any(event["event"] == "diagnostic_error" for event in events)
    assert all(
        flag not in command
        for command in commands
        for flag in ["--lazy", "--force", "--kill"]
    )


def test_finish_failure_does_not_report_completed_or_close_mapper(tmp_path):
    installer = engine(tmp_path)
    installer.mounted = installer.mapper_open = installer.verified = True
    events, commands = [], []
    installer.r.emit_callback = events.append

    def run(args, **kwargs):
        commands.append(args)
        if args[0] == "umount":
            raise CommandError("busy")

    installer.r.run = run
    with pytest.raises(UnmountError):
        installer.finish()
    installer.cleanup()
    assert installer.mounted and installer.mapper_open
    assert not any(command[0] == "cryptsetup" for command in commands)
    assert any(
        event["event"] == "finalization_required" and event["system_verified"]
        for event in events
    )
    assert not any(event["event"] == "complete" for event in events)


def test_finish_after_blocker_removed_only_syncs_and_unmounts(tmp_path):
    installer = engine(tmp_path)
    installer.mounted = installer.mapper_open = True
    commands = []
    installer.r.run = lambda args, **kwargs: commands.append(args)
    installer.finish()
    assert [command[0] for command in commands] == ["sync", "umount", "cryptsetup"]
    assert not installer.mounted and not installer.mapper_open
