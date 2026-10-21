from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from arch_gui_installer.core.config import DeviceIdentity


def read_command(args: list[str]) -> str:
    return subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    ).stdout


@dataclass
class Disk:
    identity: DeviceIdentity
    transport: str = ""
    rotational: bool = False
    readonly: bool = False
    partitions: list[dict] = field(default_factory=list)
    mounted: bool = False


def walk(node: dict):
    yield node
    for child in node.get("children", []):
        yield from walk(child)


def is_in_use(node: dict) -> bool:
    return any(
        any(n.get("mountpoints") or []) or n.get("type") not in {"disk", "part"}
        for n in walk(node)
    )


def disks() -> list[Disk]:
    data = json.loads(
        read_command(
            [
                "lsblk",
                "--json",
                "--bytes",
                "--paths",
                "--output",
                "NAME,TYPE,SIZE,MODEL,SERIAL,WWN,MAJ:MIN,TRAN,ROTA,RO,MOUNTPOINTS,FSTYPE,PARTUUID,PARTTYPE,START,LOG-SEC,PARTN,LABEL",
            ]
        )
    )
    found = []
    links = sorted(Path("/dev/disk/by-id").glob("*"))
    for node in data["blockdevices"]:
        if node["type"] != "disk" or node["name"].startswith(("/dev/zram", "/dev/ram")):
            continue
        path = node["name"]
        by_id = next(
            (
                str(p)
                for p in links
                if p.resolve() == Path(path) and "-part" not in p.name
            ),
            "",
        )
        ident = DeviceIdentity(
            path,
            node["maj:min"],
            int(node["size"]),
            (node.get("model") or "").strip(),
            node.get("serial") or "",
            node.get("wwn") or "",
            by_id,
        )
        found.append(
            Disk(
                ident,
                node.get("tran") or "",
                bool(node.get("rota")),
                bool(node.get("ro")),
                node.get("children", []),
                is_in_use(node),
            )
        )
    return found


def check_identity(expected: DeviceIdentity, require_unused: bool = True) -> Disk:
    current = next((d for d in disks() if d.identity.path == expected.path), None)
    if not current or current.identity != expected:
        raise RuntimeError("Disk identity changed. Rescan and review a new plan.")
    if current.readonly or (require_unused and current.mounted):
        raise RuntimeError(
            "Refusing a read-only disk or a disk with mounted filesystems/swap or active mappings"
        )
    return current


def hardware() -> dict:
    cpuinfo = Path("/proc/cpuinfo").read_text()
    pci = read_command(["lspci", "-nnk"]) if Path("/usr/bin/lspci").exists() else ""
    gpus = [
        line
        for line in pci.splitlines()
        if any(
            s in line for s in ("VGA compatible", "3D controller", "Display controller")
        )
    ]
    return {
        "firmware": "uefi" if Path("/sys/firmware/efi").exists() else "bios",
        "cpu": "intel"
        if "GenuineIntel" in cpuinfo
        else "amd"
        if "AuthenticAMD" in cpuinfo
        else "none",
        "gpus": gpus,
        "pci": pci,
        "laptop": bool(list(Path("/sys/class/power_supply").glob("BAT*"))),
        "network": [p.name for p in Path("/sys/class/net").iterdir()],
        "bluetooth": Path("/sys/class/bluetooth").exists(),
    }
