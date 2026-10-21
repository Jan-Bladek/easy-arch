#!/usr/bin/env python3
"""Run INSIDE a disposable Arch VM with a dedicated serial-tagged test disk.
No host disks accepted. Credentials supplied through environment, never saved.
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from arch_gui_installer.core.config import Config, Partition, Secrets
from arch_gui_installer.hardware.detect import disks, hardware
from arch_gui_installer.installation.engine import Engine
from arch_gui_installer.installation.runner import Runner
from arch_gui_installer.main import build_plan
from arch_gui_installer.storage.plan import ESP, LINUX, partition_path

parser = argparse.ArgumentParser()
parser.add_argument("--desktop", choices=["kde", "hyprland"], default="kde")
parser.add_argument("--filesystem", choices=["ext4", "btrfs", "xfs"], default="ext4")
parser.add_argument("--encrypt", action="store_true")
parser.add_argument("--firewall", action="store_true")
parser.add_argument(
    "--backend",
    action="store_true",
    help="Exercise the production JSON/credential-pipe CLI boundary",
)
parser.add_argument(
    "--manual",
    action="store_true",
    help="Prepare and verify an existing ESP + preserved data partition",
)
args = parser.parse_args()
matching = [
    d for d in disks() if d.identity.serial == "EASYARCH-TEST-TARGET" and not d.mounted
]
if len(matching) != 1:
    raise SystemExit(
        "Exactly one unmounted VM test disk with serial EASYARCH-TEST-TARGET is required"
    )
password = os.environ.pop("EASYARCH_TEST_PASSWORD")
config = Config(
    matching[0].identity,
    firmware=hardware()["firmware"],
    desktop=args.desktop,
    filesystem=args.filesystem,
    encrypt=args.encrypt,
    ssh=True,
    firewall=args.firewall,
    bluetooth=False,
    bootloader="systemd-boot" if hardware()["firmware"] == "uefi" else "grub",
    packages=["git"],
    snapshots=args.filesystem == "btrfs",
)
sentinel_mount = Path("/mnt/easy-arch-test-sentinel")
if args.manual:
    if args.encrypt or config.firmware != "uefi":
        raise SystemExit(
            "This preservation test requires UEFI and unencrypted manual mode"
        )
    prepare = Runner(lambda event: print(json.dumps(event), flush=True))
    disk = config.device.path
    table = f"label: gpt\nstart=1MiB, size=1024MiB, type={ESP}\nstart=1025MiB, size=24576MiB, type={LINUX}\nstart=25601MiB, type={LINUX}\n"
    prepare.run(["sfdisk", "--wipe", "always", disk], stdin=table.encode())
    prepare.run(["udevadm", "settle"])
    prepare.run(["mkfs.fat", "-F", "32", partition_path(disk, 1)])
    prepare.run(["mkfs.ext4", "-F", partition_path(disk, 3)])
    sentinel_mount.mkdir(exist_ok=True)
    for number in [1, 3]:
        prepare.run(["mount", partition_path(disk, number), str(sentinel_mount)])
        try:
            (sentinel_mount / "easy-arch-preserve-me.txt").write_text(
                "PRESERVED-DATA-2026"
            )
        finally:
            prepare.run(["umount", str(sentinel_mount)])
    config.mode = "manual"
    refreshed = next(d for d in disks() if d.identity == config.device)
    config.partitions = [
        Partition(
            int(p["partn"]),
            int(p["start"]) * 512 // 1024**2,
            int(p["size"]) // 1024**2,
            "vfat"
            if p["partn"] == 1
            else config.filesystem
            if p["partn"] == 2
            else "ext4",
            "/boot" if p["partn"] == 1 else "/" if p["partn"] == 2 else "",
            "format" if p["partn"] == 2 else "preserve",
            p["partuuid"],
            "esp" if p["partn"] == 1 else "data",
        )
        for p in refreshed.partitions
    ]
plan = build_plan(config)
Path("/root/easy-arch-test-plan.json").write_text(json.dumps(plan.to_dict(), indent=2))
runner = Runner(
    lambda event: print(json.dumps(event), flush=True),
    Path("/var/log/easy-arch")
    / f"vm-{args.desktop}-{args.filesystem}-{os.getpid()}.jsonl",
)
try:
    if args.backend:
        payload = (
            json.dumps(asdict(Secrets(password, luks_password=password))).encode()
            + b"\n"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "arch_gui_installer",
                "--apply",
                "/root/easy-arch-test-plan.json",
                "--approval",
                plan.digest(),
            ],
            input=payload,
        )
        if result.returncode:
            raise SystemExit(result.returncode)
    else:
        Engine(plan, Secrets(password, luks_password=password), runner).install(
            plan.digest()
        )
    if args.manual:
        for number in [1, 3]:
            runner.run(
                [
                    "mount",
                    "-o",
                    "ro",
                    partition_path(config.device.path, number),
                    str(sentinel_mount),
                ]
            )
            try:
                assert (
                    sentinel_mount / "easy-arch-preserve-me.txt"
                ).read_text() == "PRESERVED-DATA-2026"
            finally:
                runner.run(["umount", str(sentinel_mount)])
        runner.event("preservation_verified", partitions=[1, 3])
finally:
    runner.close()
