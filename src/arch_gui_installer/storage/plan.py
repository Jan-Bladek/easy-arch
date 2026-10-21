from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from arch_gui_installer.core.config import Config, Partition

ESP = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
BIOS = "21686148-6449-6e6f-744e-656564454649"
LINUX = "0fc63daf-8483-4772-8e79-3d69d8477de4"
SWAP = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"


def partition_path(disk: str, number: int) -> str:
    return f"{disk}{'p' if disk[-1].isdigit() else ''}{number}"


def layout(config: Config) -> list[Partition]:
    if config.mode == "manual":
        return config.partitions
    end = config.device.size // 1024**2 - 2
    boot = Partition(
        1,
        1,
        1024 if config.firmware == "uefi" else 2,
        "vfat" if config.firmware == "uefi" else "bios",
        "/boot" if config.firmware == "uefi" else "",
        "create",
        role="esp" if config.firmware == "uefi" else "bios",
    )
    start = boot.start_mib + boot.size_mib
    result = [boot]
    if config.firmware == "bios":
        result.append(Partition(2, start, 1024, "ext4", "/boot", "create"))
        start += 1024
    root = Partition(
        len(result) + 1,
        start,
        end - start - config.swap_mib,
        config.filesystem,
        "/",
        "create",
    )
    result.append(root)
    if config.swap_mib:
        result.append(
            Partition(
                len(result) + 1,
                end - config.swap_mib,
                config.swap_mib,
                "swap",
                "",
                "create",
            )
        )
    return result


@dataclass
class Plan:
    config: dict
    partitions: list[dict]
    packages: list[str]
    hardware: dict
    operations: list[str]
    schema: int = 1

    def to_dict(self):
        return asdict(self)

    def digest(self):
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True).encode()
        ).hexdigest()


def make_plan(config: Config, packages: list[str], hw: dict) -> Plan:
    config.validate()
    parts = layout(config)
    operations = [
        "Check live environment, identity, mounts, network, clock and target space",
        "Synchronize isolated official repository databases; resolve and download packages",
    ]
    if config.mode == "auto":
        operations += [f"ERASE partition table on {config.device.path}; create GPT"]
    for p in parts:
        name = partition_path(config.device.path, p.number)
        if p.action == "delete":
            operations.append(f"DELETE {name} PARTUUID={p.partuuid}")
        elif p.action == "preserve":
            operations.append(
                f"PRESERVE {name} PARTUUID={p.partuuid} -> {p.mountpoint or '(unmounted)'}"
            )
        else:
            operations.append(
                f"{p.action.upper()} {name}: {p.start_mib} MiB + {p.size_mib} MiB, {p.filesystem}, {p.mountpoint}"
            )
    if config.encrypt:
        operations += [
            f"LUKS2 root encryption: {config.pbkdf}; /boot remains unencrypted"
        ]
    operations += [
        "Mount filesystems and Btrfs subvolumes where selected",
        f"Install {len(packages)} explicitly selected packages plus resolved dependencies",
        "Generate fstab, locale, users, network and desktop configuration",
        "Generate initramfs and install bootloader",
        "Verify installed files, packages and enabled services; unmount",
    ]
    return Plan(config.to_dict(), [asdict(p) for p in parts], packages, hw, operations)
