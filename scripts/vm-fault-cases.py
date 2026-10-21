#!/usr/bin/env python3
"""Real preflight/transaction failures INSIDE a disposable serial-tagged VM.
No formatting is allowed; compare the actual partition-table dump after each case.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from arch_gui_installer.core.config import Config, Secrets
from arch_gui_installer.hardware.detect import disks, hardware, read_command
from arch_gui_installer.installation.engine import Engine
from arch_gui_installer.installation.runner import Runner
from arch_gui_installer.main import build_plan
from arch_gui_installer.packages.catalog import Catalog

parser = argparse.ArgumentParser()
parser.add_argument("--network-child", action="store_true")
args = parser.parse_args()
found = [
    d for d in disks() if d.identity.serial == "EASYARCH-TEST-TARGET" and not d.mounted
]
if len(found) != 1:
    raise SystemExit("A dedicated unused EASYARCH-TEST-TARGET VM disk is required")
device = found[0].identity
config = Config(
    device,
    firmware=hardware()["firmware"],
    bootloader="systemd-boot" if hardware()["firmware"] == "uefi" else "grub",
)
before = read_command(["sfdisk", "--dump", device.path])


def assert_untouched():
    assert read_command(["sfdisk", "--dump", device.path]) == before, (
        "Partition table changed during a fault test"
    )


def fail_install(c, expected):
    plan = build_plan(c)
    events = []
    runner = Runner(events.append)
    installer = Engine(plan, Secrets("test-only-fault-password"), runner)

    def forbidden():
        raise AssertionError("A destructive stage was reached during a fault test")

    installer.storage = forbidden
    try:
        installer.install(plan.digest())
    except Exception as error:
        if not isinstance(error, expected):
            raise
        assert_untouched()
        print(
            json.dumps({"fault": str(error), "disk_unchanged": True, "events": events}),
            flush=True,
        )
        return str(error)
    raise AssertionError("Fault test unexpectedly succeeded")


if args.network_child:
    import socket

    from arch_gui_installer.installation.runner import CommandError

    fail_install(config, (socket.gaierror, CommandError))
else:
    subprocess.run(
        ["unshare", "--net", sys.executable, __file__, "--network-child"],
        check=True,
        timeout=90,
    )
    assert_untouched()
    config.packages = ["easyarch-package-does-not-exist-2026"]
    try:
        build_plan(config)
    except ValueError as error:
        assert "unavailable" in str(error)
        print(
            json.dumps({"fault": "unavailable_package", "disk_unchanged": True}),
            flush=True,
        )
    else:
        raise AssertionError("Unavailable package was accepted")
    catalog = Catalog()
    config.packages = [max(catalog.packages.values(), key=lambda p: p.size).name]
    config.swap_mib = 16384
    message = fail_install(config, RuntimeError)
    assert "capacity" in message, message
    print(
        json.dumps({"fault": "insufficient_capacity", "disk_unchanged": True}),
        flush=True,
    )
