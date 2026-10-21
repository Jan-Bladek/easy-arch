import json
import sys

import pytest

import arch_gui_installer.main as cli
from arch_gui_installer.core.config import Config, DeviceIdentity
from arch_gui_installer.storage.plan import make_plan


def test_dry_run_only_emits_plan(tmp_path, monkeypatch, capsys):
    c = Config(DeviceIdentity("/dev/vdz", "9:0", 32 * 1024**3))
    path = tmp_path / "config.json"
    path.write_text(json.dumps(c.to_dict()))
    monkeypatch.setattr(sys, "argv", ["easy-arch", "--dry-run", "--config", str(path)])
    monkeypatch.setattr(cli.resource, "setrlimit", lambda *args: None)
    monkeypatch.setattr(
        cli, "build_plan", lambda config: make_plan(config, ["base"], {})
    )
    assert cli.main() == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True
    assert "password" not in json.dumps(data)


def test_dry_run_cannot_apply(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["easy-arch", "--dry-run", "--apply", "anything.json"]
    )
    monkeypatch.setattr(cli.resource, "setrlimit", lambda *args: None)
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2
