from dataclasses import replace

import pytest

import arch_gui_installer.hardware.detect as detect
from arch_gui_installer.core.config import DeviceIdentity
from arch_gui_installer.hardware.detect import Disk, check_identity


def test_reject_disk_name_reuse(monkeypatch):
    original = DeviceIdentity("/dev/sda", "8:0", 32 * 1024**3, "model", "serial-a")
    monkeypatch.setattr(
        detect, "disks", lambda: [Disk(replace(original, serial="serial-b"))]
    )
    with pytest.raises(RuntimeError, match="identity changed"):
        check_identity(original)


def test_reject_mounted_disk(monkeypatch):
    device = DeviceIdentity("/dev/sda", "8:0", 32 * 1024**3)
    monkeypatch.setattr(detect, "disks", lambda: [Disk(device, mounted=True)])
    with pytest.raises(RuntimeError, match="mounted"):
        check_identity(device)


def test_unmounted_active_mapping_is_still_in_use():
    from arch_gui_installer.hardware.detect import is_in_use

    disk = {
        "type": "disk",
        "mountpoints": [],
        "children": [
            {
                "type": "part",
                "mountpoints": [],
                "children": [{"type": "crypt", "mountpoints": []}],
            }
        ],
    }
    assert is_in_use(disk)
    disk["children"][0]["children"] = []
    assert not is_in_use(disk)
