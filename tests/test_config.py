import json
from dataclasses import replace

import pytest

from arch_gui_installer.core.config import (
    Config,
    DeviceIdentity,
    Partition,
    Secrets,
    ValidationError,
)
from arch_gui_installer.storage.plan import layout, make_plan, partition_path


@pytest.fixture
def config():
    return Config(
        DeviceIdentity("/dev/nvme9n1", "259:90", 64 * 1024**3, "Test", "TEST-SERIAL")
    )


def test_serialization_excludes_secrets(config):
    assert Config.from_dict(config.to_dict()) == config
    assert "password" not in json.dumps(config.to_dict())
    assert "secret" not in repr(Secrets("secret", "secret", "secret"))


@pytest.mark.parametrize(
    "changes",
    [
        {"username": "root"},
        {"hostname": "$(id)"},
        {"packages": ["--overwrite"]},
        {"packages": ["a;id"]},
        {"timezone": "../../etc"},
        {"keyboard": "pl\nHOOKS="},
        {"firmware": "bios", "bootloader": "systemd-boot"},
        {"kernels": []},
        {"filesystem": "f2fs"},
        {"encrypt": True, "swap_mib": 1024},
        {"snapshots": True},
        {"root_locked": True, "sudo": False},
        {"dns": ["bad"]},
        {"mode": "nonsense"},
        {"gpu": "legacy-nvidia"},
    ],
)
def test_reject_invalid(config, changes):
    with pytest.raises(ValidationError):
        replace(config, **changes).validate()


@pytest.mark.parametrize(
    "firmware,loader,fs,encrypt",
    [
        ("uefi", "systemd-boot", "ext4", False),
        ("uefi", "grub", "btrfs", True),
        ("bios", "grub", "xfs", False),
        ("bios", "grub", "ext4", True),
    ],
)
def test_layout_bootability(config, firmware, loader, fs, encrypt):
    c = replace(
        config, firmware=firmware, bootloader=loader, filesystem=fs, encrypt=encrypt
    )
    c.validate()
    parts = layout(c)
    assert len([p for p in parts if p.mountpoint == "/"]) == 1
    assert any(p.mountpoint == "/boot" and p.filesystem != "bios" for p in parts)
    assert max(p.start_mib + p.size_mib for p in parts) < c.device.size // 1024**2
    if firmware == "bios":
        assert any(p.role == "bios" for p in parts)
    for p, q in zip(parts, parts[1:]):
        assert p.start_mib + p.size_mib <= q.start_mib


def test_manual_preserve(config):
    c = replace(
        config,
        mode="manual",
        partitions=[
            Partition(1, 1, 1024, "vfat", "/boot", "preserve", "esp-id", "esp"),
            Partition(2, 1025, 30000, "ext4", "/", "format", "root-id"),
            Partition(3, 31025, 4000, "ntfs", "", "preserve", "windows-id"),
        ],
    )
    c.validate()
    assert layout(c)[2].action == "preserve"
    c.partitions[1].start_mib = 100
    with pytest.raises(ValidationError, match="Overlapping"):
        c.validate()


def test_esp_delete_blocked(config):
    c = replace(
        config,
        mode="manual",
        partitions=[
            Partition(1, 1, 1024, "vfat", "", "delete", "esp-id", "esp"),
            Partition(2, 1025, 30000, "ext4", "/", "format", "root-id"),
        ],
    )
    with pytest.raises(ValidationError, match="Deleting an existing ESP"):
        c.validate()


def test_approval_bound_to_config(config):
    a = make_plan(config, ["base"], {"cpu": "intel"})
    b = make_plan(replace(config, hostname="changed"), ["base"], {"cpu": "intel"})
    assert a.digest() != b.digest()
    assert a.digest() == a.digest()


def test_device_names():
    assert partition_path("/dev/nvme0n1", 2) == "/dev/nvme0n1p2"
    assert partition_path("/dev/sda", 2) == "/dev/sda2"


def test_secrets(config):
    with pytest.raises(ValidationError):
        Secrets("short").validate(config)
    with pytest.raises(ValidationError):
        Secrets("longpassword\nroot:evil").validate(config)
    s = Secrets("valid-password")
    s.validate(config)
    s.clear()
    assert not s.user_password


def test_manual_fat_root_rejected(config):
    c = replace(
        config,
        mode="manual",
        partitions=[
            Partition(1, 1, 1024, "vfat", "/boot", "preserve", "esp-id", "esp"),
            Partition(2, 1025, 30000, "vfat", "/", "format", "root-id"),
        ],
    )
    with pytest.raises(ValidationError):
        c.validate()
