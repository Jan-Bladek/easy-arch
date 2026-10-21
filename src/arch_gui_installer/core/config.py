"""Serializable intent. Credentials deliberately live in a separate, non-serializable object."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from zoneinfo import available_timezones


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class DeviceIdentity:
    path: str
    major_minor: str
    size: int
    model: str = ""
    serial: str = ""
    wwn: str = ""
    by_id: str = ""


@dataclass
class Partition:
    number: int
    start_mib: int
    size_mib: int
    filesystem: str = "ext4"
    mountpoint: str = ""
    action: str = "preserve"  # preserve, format, create, delete
    partuuid: str = ""
    role: str = "data"  # data, esp, bios


@dataclass
class Config:
    device: DeviceIdentity
    firmware: str = "uefi"
    mode: str = "auto"
    filesystem: str = "ext4"
    partitions: list[Partition] = field(default_factory=list)
    bootloader: str = "systemd-boot"
    kernels: list[str] = field(default_factory=lambda: ["linux"])
    desktop: str = "kde"
    packages: list[str] = field(default_factory=list)
    software: list[str] = field(default_factory=list)
    gpu: str = "auto"
    cpu: str = "auto"
    encrypt: bool = False
    pbkdf: str = "argon2id"
    swap_mib: int = 0
    zram: bool = True
    snapshots: bool = False
    groups: list[str] = field(default_factory=list)
    username: str = "arch"
    hostname: str = "easy-arch"
    locale: str = "pl_PL.UTF-8"
    timezone: str = "Europe/Warsaw"
    keyboard: str = "pl"
    root_locked: bool = True
    sudo: bool = True
    bluetooth: bool = True
    ssh: bool = False
    firewall: bool = True
    ntp: bool = True
    trim: bool = True
    import_network: bool = False
    dns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        data = dict(data)
        data["device"] = DeviceIdentity(**data["device"])
        data["partitions"] = [Partition(**p) for p in data.get("partitions", [])]
        return cls(**data)

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True).encode()
        ).hexdigest()

    def validate(self) -> None:
        errors: list[str] = []

        def require(test: bool, message: str) -> None:
            if not test:
                errors.append(message)

        require(self.firmware in {"uefi", "bios"}, "Invalid firmware")
        require(self.mode in {"auto", "manual"}, "Invalid partitioning mode")
        require(
            self.filesystem in {"ext4", "btrfs", "xfs"},
            "Unsupported filesystem (F2FS is not qualified)",
        )
        require(self.bootloader in {"systemd-boot", "grub"}, "Invalid bootloader")
        require(
            self.firmware == "uefi" or self.bootloader == "grub", "BIOS requires GRUB"
        )
        require(self.desktop in {"kde", "hyprland"}, "Invalid desktop")
        require(
            self.gpu in {"auto", "mesa", "nvidia-open", "nouveau"}, "Invalid GPU driver"
        )
        require(self.cpu in {"auto", "intel", "amd", "none"}, "Invalid CPU selection")
        require(
            bool(self.kernels)
            and len(set(self.kernels)) == len(self.kernels)
            and set(self.kernels)
            <= {"linux", "linux-lts", "linux-zen", "linux-hardened"},
            "Invalid kernel selection",
        )
        require(self.pbkdf in {"argon2id", "pbkdf2"}, "Invalid LUKS PBKDF")
        require(
            self.device.path.startswith("/dev/") and ".." not in self.device.path,
            "Invalid block device",
        )
        require(self.device.size >= 20 * 1024**3, "At least 20 GiB is required")
        require(
            0 <= self.swap_mib <= self.device.size // 1024**2 - 16384,
            "Swap leaves insufficient root space",
        )
        require(
            not self.encrypt or self.swap_mib == 0,
            "Encrypted installations use zram; unencrypted disk swap is disallowed",
        )
        require(
            not self.snapshots or self.filesystem == "btrfs", "Snapshots require Btrfs"
        )
        require(
            bool(re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}", self.username))
            and self.username
            not in {"root", "nobody", "daemon", "bin", "systemd-network"},
            "Invalid username",
        )
        require(
            bool(
                re.fullmatch(
                    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", self.hostname
                )
            ),
            "Invalid hostname",
        )
        require(
            self.locale in {"pl_PL.UTF-8", "en_US.UTF-8", "en_GB.UTF-8", "de_DE.UTF-8"},
            "Unsupported locale",
        )
        require(self.timezone in available_timezones(), "Invalid timezone")
        require(
            bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", self.keyboard)), "Invalid keymap"
        )
        require(
            self.sudo or not self.root_locked, "An administrator account is required"
        )
        require(
            set(self.groups)
            <= {
                "audio",
                "video",
                "input",
                "storage",
                "optical",
                "libvirt",
                "lp",
                "scanner",
                "uucp",
            },
            "Unsupported supplementary user group",
        )
        for package in self.packages:
            require(
                bool(re.fullmatch(r"[a-zA-Z0-9@_+][a-zA-Z0-9@._+-]*", package)),
                f"Invalid package name: {package!r}",
            )
        require(
            set(self.software)
            <= {"development", "networking", "virtualization", "desktop"},
            "Unknown software profile",
        )
        for dns in self.dns:
            try:
                ipaddress.ip_address(dns)
            except ValueError:
                errors.append("DNS must be an IP address")
        if self.mode == "manual":
            active = [p for p in self.partitions if p.action != "delete"]
            require(
                len({p.number for p in self.partitions}) == len(self.partitions),
                "Duplicate partition number",
            )
            require(
                len([p for p in active if p.mountpoint == "/"]) == 1,
                "Exactly one root mount is required",
            )
            mounts = [p.mountpoint for p in active if p.mountpoint]
            require(len(mounts) == len(set(mounts)), "Duplicate mountpoints")
            require(
                not self.encrypt,
                "Manual LUKS is not qualified; use automatic partitioning",
            )
            require(
                self.swap_mib == 0,
                "Manual mode: swap partitions are not managed; use zram",
            )
            root = next((p for p in active if p.mountpoint == "/"), None)
            require(
                root is not None and root.action in {"format", "create"},
                "Root must be explicitly formatted (no in-place overwrite)",
            )
            if root:
                require(
                    root.filesystem == self.filesystem,
                    "Root filesystem must match the selected filesystem",
                )
                require(
                    root.size_mib >= 16384, "Root partition must have at least 16 GiB"
                )
            for p in self.partitions:
                require(
                    p.action in {"preserve", "format", "create", "delete"},
                    "Invalid partition action",
                )
                require(p.role in {"data", "esp", "bios"}, "Invalid partition role")
                require(
                    1 <= p.number <= 128 and p.start_mib >= 1 and p.size_mib > 0,
                    "Invalid partition geometry",
                )
                require(
                    p.start_mib + p.size_mib <= self.device.size // 1024**2 - 1,
                    "Partition exceeds disk",
                )
                require(
                    p.filesystem
                    in {
                        "ext4",
                        "btrfs",
                        "xfs",
                        "vfat",
                        "bios",
                        "ntfs",
                        "swap",
                        "unknown",
                    },
                    "Invalid filesystem",
                )
                if p.action in {"format", "create"}:
                    require(
                        p.filesystem in {"ext4", "btrfs", "xfs", "vfat", "bios"},
                        "Unsupported formatting operation",
                    )
                if p.mountpoint:
                    require(
                        p.filesystem in {"ext4", "btrfs", "xfs", "vfat"},
                        "Unsupported mounted filesystem",
                    )
                    require(
                        p.filesystem != "vfat" or p.mountpoint == "/boot",
                        "FAT is only allowed for /boot",
                    )
                    pp = PurePosixPath(p.mountpoint)
                    require(
                        p.mountpoint.startswith("/")
                        and ".." not in pp.parts
                        and str(pp) == p.mountpoint,
                        "Invalid mountpoint",
                    )
                    require(
                        p.mountpoint
                        in {"/", "/boot", "/home", "/var/log", "/srv", "/opt"},
                        "Unsupported mountpoint",
                    )
                if p.role == "esp" and p.action == "delete":
                    require(
                        False,
                        "Deleting an existing ESP is intentionally blocked; preserve it",
                    )
            for i, p in enumerate(active):
                for q in active[i + 1 :]:
                    require(
                        p.start_mib + p.size_mib <= q.start_mib
                        or q.start_mib + q.size_mib <= p.start_mib,
                        "Overlapping partitions",
                    )
            if self.firmware == "uefi":
                esp = [p for p in active if p.role == "esp" and p.mountpoint == "/boot"]
                require(
                    len(esp) == 1
                    and esp[0].filesystem == "vfat"
                    and esp[0].size_mib >= 1024,
                    "UEFI requires a FAT ESP >= 1 GiB mounted at /boot",
                )
            else:
                require(
                    any(p.role == "bios" and p.size_mib >= 1 for p in active),
                    "BIOS/GPT requires a BIOS boot partition",
                )
        if errors:
            raise ValidationError("\n".join(errors))


@dataclass(repr=False)
class Secrets:
    user_password: str = ""
    root_password: str = ""
    luks_password: str = ""

    def validate(self, config: Config) -> None:
        for label, password, needed in [
            ("User", self.user_password, True),
            ("Root", self.root_password, not config.root_locked),
            ("LUKS", self.luks_password, config.encrypt),
        ]:
            if needed and (len(password) < 8 or any(c in password for c in "\n\r\0")):
                raise ValidationError(
                    f"{label}: password must have >=8 characters and no control separators"
                )

    def clear(self) -> None:
        self.user_password = self.root_password = self.luks_password = ""
