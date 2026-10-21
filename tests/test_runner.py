import sys

import pytest

from arch_gui_installer.installation.runner import Cancelled, CommandError, Runner


def test_failure_is_failure(tmp_path):
    events = []
    runner = Runner(events.append, tmp_path / "journal")
    with pytest.raises(CommandError, match="7"):
        runner.run([sys.executable, "-c", 'import sys; print("failed"); sys.exit(7)'])
    runner.close()
    assert "failed" in (tmp_path / "journal").read_text()
    assert (tmp_path / "journal").stat().st_mode & 0o777 == 0o600


def test_password_is_never_logged(tmp_path):
    events = []
    runner = Runner(events.append, tmp_path / "journal")
    runner.run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read())"],
        stdin=b"never-log-this",
        sensitive=True,
    )
    runner.close()
    assert "never-log-this" not in (tmp_path / "journal").read_text()


def test_argument_injection_is_literal(tmp_path):
    runner = Runner(lambda e: None)
    value = "$(touch " + str(tmp_path / "oops") + "); echo bad"
    out = runner.run(
        [sys.executable, "-c", "import sys;print(sys.argv[1])", value], capture=True
    )
    assert out.strip() == value
    assert not (tmp_path / "oops").exists()


def test_cancel_blocks_next_command():
    runner = Runner(lambda e: None)
    runner.cancelled.set()
    with pytest.raises(Cancelled):
        runner.run(["/usr/bin/true"])
    runner.run(["/usr/bin/true"], cleanup=True)


def test_timeout():
    runner = Runner(lambda e: None)
    with pytest.raises(TimeoutError):
        runner.run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=1)


def test_download_command_can_be_cancelled():
    import threading
    import time

    runner = Runner(lambda e: None)
    timer = threading.Timer(0.2, runner.cancelled.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(Cancelled):
            runner.run(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                interruptible=True,
            )
        assert time.monotonic() - started < 3
    finally:
        timer.join()
