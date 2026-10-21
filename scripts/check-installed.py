#!/usr/bin/env python3
"""Password-based login and service/package diagnostics for a LOCAL QEMU VM.
Requires pexpect and EASYARCH_TEST_PASSWORD. Never logs the credential exchange.
A report of these checks is not proof that a graphical/audio test passed.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import pexpect

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--known-hosts", type=Path, required=True)
parser.add_argument("--report", type=Path, required=True)
parser.add_argument("--wait-seconds", type=int, default=300)
args = parser.parse_args()
password = os.environ.pop("EASYARCH_TEST_PASSWORD")
command = """
set -eu
printf 'EASYARCH_PASSWORD_LOGIN_OK\\n'
id
uname -r
systemctl is-active NetworkManager sddm
systemctl --failed --no-pager
loginctl list-sessions --no-pager
pacman -Q git linux networkmanager pipewire wireplumber
curl --fail --head --max-time 30 https://archlinux.org
test -z "$(systemctl --failed --no-legend --plain)"
printf 'EASYARCH_DIAGNOSTICS_FINISHED\\n'
"""
deadline = time.monotonic() + args.wait_seconds
while True:
    child = pexpect.spawn(
        "/usr/bin/ssh",
        [
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"UserKnownHostsFile={args.known_hosts.resolve()}",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "PreferredAuthentications=password",
            "-o",
            "PubkeyAuthentication=no",
            "-p",
            str(args.port),
            "arch@127.0.0.1",
            command,
        ],
        encoding="utf-8",
        timeout=120,
    )
    match = child.expect(["[Pp]assword:", pexpect.EOF, pexpect.TIMEOUT])
    if match == 0:
        break
    child.close(force=True)
    if time.monotonic() >= deadline:
        raise SystemExit("VM SSH did not become ready within the requested time")
    time.sleep(2)
child.sendline(password)
child.expect("EASYARCH_PASSWORD_LOGIN_OK")
child.expect(pexpect.EOF)
output = child.before
child.close()
report = {
    "password_login": True,
    "exit_code": child.exitstatus,
    "output": output.replace(password, "<redacted>"),
}
args.report.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
raise SystemExit(0 if child.exitstatus == 0 else 1)
