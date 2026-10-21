from __future__ import annotations

import fcntl
import json
import os
import shutil
import socket
import tempfile
import time
import uuid
from importlib.resources import files
from pathlib import Path

from arch_gui_installer.core.config import Config, Partition, Secrets, ValidationError
from arch_gui_installer.hardware.detect import check_identity, hardware
from arch_gui_installer.installation.runner import Runner
from arch_gui_installer.packages.catalog import Catalog
from arch_gui_installer.storage.fstab import without_live_swap
from arch_gui_installer.storage.plan import BIOS, ESP, LINUX, SWAP, Plan, partition_path

TARGET = Path("/mnt/easy-arch-target")
PACMAN_CONFIG = """[options]
Architecture = auto
CheckSpace
SigLevel = Required DatabaseOptional
LocalFileSigLevel = Optional
ParallelDownloads = 5
CacheDir = /var/cache/pacman/pkg
[core]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
[extra]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
[multilib]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
"""


class Engine:
    def __init__(self, plan: Plan, secrets: Secrets, runner: Runner):
        self.plan, self.secrets, self.r = plan, secrets, runner
        self.c = Config.from_dict(plan.config)
        self.parts = [Partition(**p) for p in plan.partitions]
        self.target = TARGET
        self.mapper = "easyarch-" + uuid.uuid4().hex[:12]
        self.mapper_open = False
        self.mounted = False
        self.root_device = ""
        self.luks_uuid = ""
        self.root_uuid = ""
        self.lock_fd = None
        self.work = None
        self.pacman_config = None
        self.db = None

    def chroot(self, *args: str, **kw) -> str:
        return self.r.run(["arch-chroot", str(self.target), *args], **kw)

    def write(self, relative: str, contents: str, mode: int = 0o644) -> None:
        destination = self.target / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Unsafe destination")
        for parent in [destination, *destination.parents]:
            if parent == self.target:
                break
            if parent.is_symlink():
                raise RuntimeError(f"Refusing symlink in destination: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        preserved = [
            p.mountpoint for p in self.parts if p.action == "preserve" and p.mountpoint
        ]
        is_preserved = any(
            "/" + relative == mount
            or ("/" + relative).startswith(mount.rstrip("/") + "/")
            for mount in preserved
        )
        if destination.exists() and is_preserved:
            backup = destination.with_name(
                destination.name + ".easy-arch-backup-" + uuid.uuid4().hex[:8]
            )
            original = destination.stat()
            shutil.copy2(destination, backup, follow_symlinks=False)
            os.chown(backup, original.st_uid, original.st_gid)
            self.r.event("backup", path=str(backup.relative_to(self.target)))
        fd, temporary = tempfile.mkstemp(prefix=".easy-arch-", dir=destination.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.r.event("file", path="/" + relative)

    def stage(self, index: int, name: str, fn) -> None:
        self.r.check_cancel()
        self.r.event("stage", index=index, total=9, name=name, status="running")
        started = time.monotonic()
        try:
            fn()
        except BaseException:
            self.r.event(
                "stage",
                index=index,
                total=9,
                name=name,
                status="failed",
                seconds=time.monotonic() - started,
            )
            raise
        self.r.event(
            "stage",
            index=index,
            total=9,
            name=name,
            status="done",
            seconds=time.monotonic() - started,
        )

    def install(self, approval: str) -> None:
        if approval != self.plan.digest():
            raise ValidationError("Approval does not match the reviewed plan")
        self.c.validate()
        self.secrets.validate(self.c)
        try:
            for i, (name, fn) in enumerate(
                [
                    ("Preflight", self.preflight),
                    ("Repository and package transaction", self.packages),
                    ("Partition and format", self.storage),
                    ("Mount filesystems", self.mount),
                    ("Install Arch Linux packages", self.pacstrap),
                    ("Configure system and desktop", self.configure),
                    ("Initramfs and bootloader", self.bootloader),
                    ("Verify installed system", self.verify),
                    ("Flush and unmount", self.finish),
                ],
                1,
            ):
                self.stage(i, name, fn)
            self.r.event(
                "complete",
                message="Installation completed; reboot and desktop still require a boot test",
            )
        finally:
            self.secrets.clear()
            self.cleanup()
            if self.work:
                self.work.cleanup()
            if self.lock_fd is not None:
                os.close(self.lock_fd)
                self.lock_fd = None

    def preflight(self):
        if os.geteuid() != 0:
            raise PermissionError("Installation requires a privileged backend")
        if (
            not Path("/etc/arch-gui-installer/live-environment").is_file()
            and not Path("/run/archiso/bootmnt").is_mount()
        ):
            raise RuntimeError(
                "Use the installer live ISO. Applying on an ordinary host is blocked."
            )
        self.lock_fd = os.open(
            "/run/easy-arch-install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another installation is already running") from exc
        if hardware()["firmware"] != self.c.firmware:
            raise RuntimeError("Firmware mode differs from the reviewed plan")
        secure = list(Path("/sys/firmware/efi/efivars").glob("SecureBoot-*"))
        if secure and secure[0].read_bytes()[-1] == 1:
            raise RuntimeError(
                "Secure Boot signing is not supported; disable Secure Boot before installing"
            )
        for tool in [
            "modprobe",
            "sfdisk",
            "wipefs",
            "mount",
            "umount",
            "udevadm",
            "lsblk",
            "blkid",
            "pacman",
            "pacstrap",
            "arch-chroot",
            "genfstab",
            "mkfs.ext4",
            "mkfs.fat",
            "cryptsetup",
            "btrfs",
            "mkfs.xfs",
        ]:
            if not shutil.which(tool):
                raise RuntimeError(f"Missing live dependency: {tool}")
        check_identity(self.c.device)
        if self.c.encrypt:
            self.r.run(["modprobe", "dm_crypt"])
        if self.c.filesystem in {"btrfs", "xfs"}:
            self.r.run(["modprobe", self.c.filesystem])
        if (
            self.target.is_symlink()
            or self.target.is_mount()
            or (self.target.exists() and any(self.target.iterdir()))
        ):
            raise RuntimeError("Installation target is not an empty ordinary directory")
        if self.c.mode == "manual":
            self.validate_existing()
        socket.getaddrinfo("geo.mirror.pkgbuild.com", 443)
        self.r.run(
            [
                "curl",
                "--fail",
                "--location",
                "--head",
                "--max-time",
                "30",
                "https://geo.mirror.pkgbuild.com/core/os/x86_64/core.db",
            ],
            timeout=40,
        )
        if (
            self.c.keyboard
            not in self.r.run(["localectl", "list-keymaps"], capture=True).splitlines()
        ):
            raise ValidationError("Console keymap unavailable in live environment")
        # Verify time, rather than silently accepting TLS/signature errors with a bad RTC.
        if time.gmtime().tm_year < 2025:
            raise RuntimeError(
                "System clock is invalid; synchronize it before installation"
            )
        self.work = tempfile.TemporaryDirectory(prefix="easy-arch-", dir="/run")
        work = Path(self.work.name)
        self.pacman_config = work / "pacman.conf"
        self.pacman_config.write_text(PACMAN_CONFIG)
        self.db = work / "db"
        self.db.mkdir()
        (self.db / "local").mkdir()

    def validate_existing(self):
        table = json.loads(
            self.r.run(["sfdisk", "--json", self.c.device.path], capture=True)
        )["partitiontable"]
        if table["label"] != "gpt":
            raise ValidationError(
                "Manual mode currently requires GPT; automatic mode creates GPT"
            )
        existing = {p["node"]: p for p in table["partitions"]}
        sector = int(table.get("sectorsize", 512))
        for p in self.parts:
            node = partition_path(self.c.device.path, p.number)
            actual = existing.get(node)
            if p.action == "create":
                if actual:
                    raise RuntimeError("New partition number already exists")
                continue
            if (
                not actual
                or not p.partuuid
                or actual.get("uuid", "").lower() != p.partuuid.lower()
            ):
                raise RuntimeError("Existing partition UUID changed")
            if actual.get("type", "").lower() == ESP and p.action == "delete":
                raise RuntimeError(
                    "Refusing deletion of an existing ESP regardless of its declared role"
                )
            if p.role == "esp" and actual.get("type", "").lower() != ESP:
                raise RuntimeError("Declared ESP does not have the EFI partition type")
            if (
                actual["start"] * sector // 1024**2 != p.start_mib
                or actual["size"] * sector // 1024**2 != p.size_mib
            ):
                raise RuntimeError(
                    "Existing partition geometry changed; resizing is not supported"
                )
        specified = {
            partition_path(self.c.device.path, p.number)
            for p in self.parts
            if p.action != "create"
        }
        if set(existing) != specified:
            raise RuntimeError(
                "All existing partitions must appear explicitly in the reviewed plan"
            )

    def packages(self):
        self.r.run(
            [
                "pacman",
                "--config",
                str(self.pacman_config),
                "--dbpath",
                str(self.db),
                "-Sy",
                "--noconfirm",
            ],
            timeout=300,
            interruptible=True,
        )
        catalog = Catalog(self.db / "sync")
        resolved = catalog.expand(self.plan.packages)
        if resolved != sorted(self.plan.packages):
            raise RuntimeError("Package set changed; review a new plan")
        if self.c.desktop == "hyprland":
            hv = catalog.packages["hyprland"].version.split(":")[-1]
            nv = catalog.packages["noctalia"].version.split(":")[-1]
            if not hv.startswith("0.56.") or not nv.startswith("5."):
                raise RuntimeError(
                    "Hyprland/Noctalia configuration is qualified for 0.56.x/5.x; update the profile before installing another family"
                )
        # A scratch installation root and empty local DB ensure conflicts are checked
        # against the new system, not against the live desktop.
        scratch = Path(self.work.name) / "resolve"
        scratch.mkdir()
        (scratch / "var/lib/pacman").mkdir(parents=True)
        transaction = self.r.run(
            [
                "pacman",
                "--config",
                str(self.pacman_config),
                "--root",
                str(scratch),
                "--dbpath",
                str(self.db),
                "-Sp",
                "--noconfirm",
                "--print-format",
                "%n",
                *resolved,
            ],
            capture=True,
        )
        dependencies = sorted(
            {
                line.strip()
                for line in transaction.splitlines()
                if line.strip() in catalog.packages
            }
        )
        if not set(resolved).issubset(dependencies):
            raise RuntimeError("Could not determine the complete package transaction")
        estimated = sum(catalog.packages[name].size for name in dependencies)
        root = next(
            p for p in self.parts if p.mountpoint == "/" and p.action != "delete"
        )
        if root.size_mib * 1024**2 < int(estimated * 1.2) + 4 * 1024**3:
            raise RuntimeError(
                "Selected packages and working space exceed root partition capacity"
            )
        self.r.event(
            "transaction", packages=dependencies, estimated_installed_bytes=estimated
        )
        # pacman --downloadonly runs a real transaction preparation, unlike --print.
        self.r.run(
            [
                "pacman",
                "--config",
                str(self.pacman_config),
                "--root",
                str(scratch),
                "--dbpath",
                str(self.db),
                "-Sw",
                "--noconfirm",
                *resolved,
            ],
            timeout=7200,
            interruptible=True,
        )
        self.r.event("packages", count=len(resolved), names=resolved)

    def storage(self):
        # Identity and mount checks are deliberately adjacent to the first write.
        check_identity(self.c.device)
        disk = self.c.device.path
        if self.c.mode == "auto":
            lines = ["label: gpt", "unit: sectors"]
            sector = int(
                self.r.run(["blockdev", "--getss", disk], capture=True).strip()
            )
            for p in self.parts:
                kind = (
                    ESP
                    if p.role == "esp"
                    else BIOS
                    if p.role == "bios"
                    else SWAP
                    if p.filesystem == "swap"
                    else LINUX
                )
                lines.append(
                    f"start={p.start_mib * 1024**2 // sector}, size={p.size_mib * 1024**2 // sector}, type={kind}"
                )
            self.r.run(
                ["sfdisk", "--wipe", "always", "--wipe-partitions", "always", disk],
                stdin=("\n".join(lines) + "\n").encode(),
            )
        else:
            self.validate_existing()
            deleted = [str(p.number) for p in self.parts if p.action == "delete"]
            if deleted:
                self.r.run(["sfdisk", "--delete", disk, *deleted])
            for p in self.parts:
                if p.action != "create":
                    continue
                kind = ESP if p.role == "esp" else BIOS if p.role == "bios" else LINUX
                # Explicit node name pins the GPT slot; sizes have explicit MiB suffix.
                entry = f"{partition_path(disk, p.number)} : start={p.start_mib}MiB, size={p.size_mib}MiB, type={kind}\n"
                self.r.run(
                    [
                        "sfdisk",
                        "--append",
                        "--wipe",
                        "never",
                        "--wipe-partitions",
                        "never",
                        disk,
                    ],
                    stdin=entry.encode(),
                )
        self.r.run(["udevadm", "settle"])
        for p in self.parts:
            if p.action not in {"create", "format"} or p.role == "bios":
                continue
            device = partition_path(disk, p.number)
            if p.mountpoint == "/" and self.c.encrypt:
                self.r.run(
                    [
                        "cryptsetup",
                        "luksFormat",
                        "--type",
                        "luks2",
                        "--pbkdf",
                        self.c.pbkdf,
                        "--batch-mode",
                        "--key-file",
                        "-",
                        device,
                    ],
                    stdin=self.secrets.luks_password.encode(),
                    sensitive=True,
                )
                self.luks_uuid = self.r.run(
                    ["cryptsetup", "luksUUID", device], capture=True
                ).strip()
                self.r.run(
                    ["cryptsetup", "open", "--key-file", "-", device, self.mapper],
                    stdin=self.secrets.luks_password.encode(),
                    sensitive=True,
                )
                self.mapper_open = True
                device = "/dev/mapper/" + self.mapper
            if p.filesystem == "ext4":
                self.r.run(["mkfs.ext4", "-F", device])
            elif p.filesystem == "btrfs":
                self.r.run(["mkfs.btrfs", "-f", device])
            elif p.filesystem == "xfs":
                self.r.run(["mkfs.xfs", "-f", device])
            elif p.filesystem == "vfat":
                self.r.run(["mkfs.fat", "-F", "32", device])
            elif p.filesystem == "swap":
                self.r.run(["mkswap", device])

    def mount(self):
        root = next(
            p for p in self.parts if p.mountpoint == "/" and p.action != "delete"
        )
        self.root_device = (
            "/dev/mapper/" + self.mapper
            if self.c.encrypt
            else partition_path(self.c.device.path, root.number)
        )
        self.target.mkdir(parents=True, exist_ok=True)
        self.r.run(["mount", self.root_device, str(self.target)])
        self.mounted = True
        if self.c.filesystem == "btrfs":
            subs = {
                "@": "",
                "@home": "home",
                "@log": "var/log",
                "@snapshots": ".snapshots",
            }
            manual_mounts = {p.mountpoint for p in self.parts if p.action != "delete"}
            if "/home" in manual_mounts:
                subs.pop("@home")
            if "/var/log" in manual_mounts:
                subs.pop("@log")
            for sub in subs:
                self.r.run(["btrfs", "subvolume", "create", str(self.target / sub)])
            self.r.run(["umount", str(self.target)])
            self.r.run(
                [
                    "mount",
                    "-o",
                    "subvol=@,compress=zstd",
                    self.root_device,
                    str(self.target),
                ]
            )
            for sub, mountpoint in subs.items():
                if not mountpoint:
                    continue
                dest = self.target / mountpoint
                dest.mkdir(parents=True, exist_ok=True)
                self.r.run(
                    [
                        "mount",
                        "-o",
                        f"subvol={sub},compress=zstd",
                        self.root_device,
                        str(dest),
                    ]
                )
        for p in sorted(
            self.parts, key=lambda p: (p.mountpoint.count("/"), p.mountpoint)
        ):
            if p.action == "delete" or p.mountpoint in {"", "/"}:
                continue
            device = partition_path(self.c.device.path, p.number)
            dest = self.target / p.mountpoint.lstrip("/")
            if dest.is_symlink():
                raise RuntimeError("Unsafe mountpoint symlink")
            dest.mkdir(parents=True, exist_ok=True)
            self.r.run(["mount", device, str(dest)])
        free = shutil.disk_usage(self.target).free
        if free < 12 * 1024**3:
            raise RuntimeError("Less than 12 GiB available on mounted root")
        (self.target / "boot").mkdir(exist_ok=True)
        boot_free = shutil.disk_usage(self.target / "boot").free
        if boot_free < len(self.c.kernels) * 180 * 1024**2:
            raise RuntimeError(
                "Boot partition lacks space for kernels and fallback initramfs"
            )
        if self.c.mode == "manual":
            for kernel in self.c.kernels:
                for name in [f"vmlinuz-{kernel}", f"initramfs-{kernel}.img"]:
                    if (self.target / "boot" / name).exists():
                        raise RuntimeError(
                            "Existing ESP contains conflicting Arch kernel paths; use a separate ESP"
                        )
        self.root_uuid = self.r.run(
            ["blkid", "-s", "UUID", "-o", "value", self.root_device], capture=True
        ).strip()

    def pacstrap(self):
        self.r.run(
            [
                "pacstrap",
                "-K",
                "-C",
                str(self.pacman_config),
                str(self.target),
                *self.plan.packages,
            ],
            timeout=7200,
        )
        self.write("etc/pacman.conf", PACMAN_CONFIG)
        fstab = self.r.run(["genfstab", "-U", str(self.target)], capture=True)
        fstab = without_live_swap(fstab)
        if self.c.swap_mib:
            swap = next(p for p in self.parts if p.filesystem == "swap")
            swap_uuid = self.r.run(
                [
                    "blkid",
                    "-s",
                    "UUID",
                    "-o",
                    "value",
                    partition_path(self.c.device.path, swap.number),
                ],
                capture=True,
            ).strip()
            fstab += f"\nUUID={swap_uuid} none swap defaults 0 0\n"
        self.write("etc/fstab", fstab)

    def configure(self):
        self.write("etc/hostname", self.c.hostname + "\n")
        self.write(
            "etc/hosts",
            f"127.0.0.1 localhost\n::1 localhost\n127.0.1.1 {self.c.hostname}.localdomain {self.c.hostname}\n",
        )
        self.write(
            "etc/locale.gen",
            self.c.locale + " UTF-8\nen_US.UTF-8 UTF-8\n"
            if self.c.locale != "en_US.UTF-8"
            else "en_US.UTF-8 UTF-8\n",
        )
        self.write("etc/locale.conf", f"LANG={self.c.locale}\n")
        self.write("etc/vconsole.conf", f"KEYMAP={self.c.keyboard}\n")
        xkb = {"uk": "gb"}.get(self.c.keyboard, self.c.keyboard)
        self.write(
            "etc/X11/xorg.conf.d/00-keyboard.conf",
            f'Section "InputClass"\n    Identifier "system-keyboard"\n    MatchIsKeyboard "on"\n    Option "XkbLayout" "{xkb}"\nEndSection\n',
        )
        self.chroot(
            "ln", "-sf", "/usr/share/zoneinfo/" + self.c.timezone, "/etc/localtime"
        )
        self.chroot("locale-gen")
        self.chroot("hwclock", "--systohc")
        for group in self.c.groups:
            self.chroot("groupadd", "-f", group)
        groups = ",".join(
            sorted(set(self.c.groups + (["wheel"] if self.c.sudo else ["users"])))
        )
        self.chroot("useradd", "-m", "-G", groups, "-s", "/bin/bash", self.c.username)
        payload = f"{self.c.username}:{self.secrets.user_password}\n"
        if not self.c.root_locked:
            payload += f"root:{self.secrets.root_password}\n"
        self.chroot("chpasswd", stdin=payload.encode(), sensitive=True)
        if self.c.root_locked:
            self.chroot("usermod", "-L", "root")
        if self.c.sudo:
            self.write(
                "etc/sudoers.d/10-easy-arch", "%wheel ALL=(ALL:ALL) ALL\n", 0o440
            )
            self.chroot("visudo", "-cf", "/etc/sudoers")
        if self.c.zram:
            self.write(
                "etc/systemd/zram-generator.conf",
                "[zram0]\nzram-size = ram / 2\ncompression-algorithm = zstd\n",
            )
        if self.c.dns:
            self.write(
                "etc/NetworkManager/conf.d/10-dns.conf",
                "[main]\ndns=systemd-resolved\n",
            )
            self.write(
                "etc/systemd/resolved.conf.d/10-dns.conf",
                "[Resolve]\nDNS=" + " ".join(self.c.dns) + "\n",
            )
            (self.target / "etc/resolv.conf").unlink(missing_ok=True)
            (self.target / "etc/resolv.conf").symlink_to(
                "/run/systemd/resolve/stub-resolv.conf"
            )
        if self.c.import_network:
            for source in Path("/etc/NetworkManager/system-connections").glob("*"):
                if source.is_file() and not source.is_symlink():
                    self.write(
                        "etc/NetworkManager/system-connections/" + source.name,
                        source.read_text(),
                        0o600,
                    )
        if self.c.desktop == "hyprland":
            base = f"home/{self.c.username}/.config/"
            resource = files("arch_gui_installer.profiles")
            for name in ["hyprland.lua", "hypridle.conf", "hyprlock.conf"]:
                self.write(
                    base + "hypr/" + name,
                    resource.joinpath(name)
                    .read_text()
                    .replace(
                        "@KEYBOARD@", {"uk": "gb"}.get(self.c.keyboard, self.c.keyboard)
                    ),
                )
            self.write(
                base + "noctalia/config.toml",
                resource.joinpath("noctalia.toml").read_text(),
            )
            self.write(
                base + "xdg-desktop-portal/portals.conf",
                "[preferred]\ndefault=hyprland;gtk\norg.freedesktop.impl.portal.FileChooser=gtk\n",
            )
            self.write(
                base + "gtk-3.0/settings.ini",
                "[Settings]\ngtk-theme-name=Adwaita-dark\ngtk-icon-theme-name=Adwaita\n",
            )
            self.write(
                base + "qt6ct/qt6ct.conf",
                "[Appearance]\nstyle=Fusion\nicon_theme=Adwaita\n",
            )
            self.write(
                "etc/sddm.conf.d/20-session.conf",
                "[General]\nDisplayServer=x11\n[Users]\nRememberLastSession=true\n",
            )
            self.chroot(
                "chown",
                "-R",
                self.c.username + ":" + self.c.username,
                f"/home/{self.c.username}/.config",
            )
        if self.c.gpu == "nvidia-open":
            self.write("etc/modprobe.d/nvidia.conf", "options nvidia_drm modeset=1\n")
        if self.c.snapshots:
            self.write(
                "etc/snapper/configs/root",
                'SUBVOLUME="/"\nFSTYPE="btrfs"\nTIMELINE_CREATE="yes"\nTIMELINE_CLEANUP="yes"\nNUMBER_CLEANUP="yes"\nNUMBER_LIMIT="10"\nNUMBER_LIMIT_IMPORTANT="5"\nTIMELINE_LIMIT_HOURLY="5"\nTIMELINE_LIMIT_DAILY="7"\nTIMELINE_LIMIT_WEEKLY="0"\nTIMELINE_LIMIT_MONTHLY="0"\nTIMELINE_LIMIT_YEARLY="0"\n',
            )
            self.write("etc/conf.d/snapper", 'SNAPPER_CONFIGS="root"\n')
        services = self.services()
        self.r.run(["systemctl", "--root", str(self.target), "enable", *services])
        self.r.run(
            ["systemctl", "--root", str(self.target), "set-default", "graphical.target"]
        )
        if self.c.firewall:
            ssh_rule = "    tcp dport 22 accept\n" if self.c.ssh else ""
            self.write(
                "etc/nftables.conf",
                "#!/usr/bin/nft -f\nflush ruleset\ntable inet filter {\n  chain input {\n    type filter hook input priority 0; policy drop;\n    ct state invalid drop\n    ct state established,related accept\n    iifname lo accept\n    meta l4proto { icmp, ipv6-icmp } accept\n    udp sport 67 udp dport 68 accept\n    udp sport 547 udp dport 546 accept\n"
                + ssh_rule
                + "  }\n  chain forward { type filter hook forward priority 0; policy drop; }\n  chain output { type filter hook output priority 0; policy accept; }\n}\n",
            )

    def services(self):
        enabled = ["NetworkManager.service", "sddm.service"]
        if self.c.ntp:
            enabled += ["systemd-timesyncd.service"]
        if self.c.trim:
            enabled += ["fstrim.timer"]
        if self.c.bluetooth:
            enabled += ["bluetooth.service"]
        if self.c.ssh:
            enabled += ["sshd.service"]
        if self.c.firewall:
            enabled += ["nftables.service"]
        if self.c.dns:
            enabled += ["systemd-resolved.service"]
        if self.c.snapshots:
            enabled += ["snapper-timeline.timer", "snapper-cleanup.timer"]
        if "virtualization" in self.c.software:
            enabled += ["libvirtd.service"]
        if self.c.desktop == "hyprland":
            enabled += ["power-profiles-daemon.service"]
        return enabled

    def bootloader(self):
        hooks = (
            "base systemd autodetect microcode modconf kms keyboard sd-vconsole block"
        )
        if self.c.encrypt:
            hooks += " sd-encrypt"
        hooks += " filesystems fsck"
        self.write("etc/mkinitcpio.conf.d/10-easy-arch.conf", f"HOOKS=({hooks})\n")
        self.chroot("mkinitcpio", "-P")
        cmdline = f"root=UUID={self.root_uuid} rw"
        if self.c.encrypt:
            cmdline += f" rd.luks.name={self.luks_uuid}=cryptroot"
        if self.c.filesystem == "btrfs":
            cmdline += " rootflags=subvol=@"
        if self.c.gpu == "nvidia-open":
            cmdline += " nvidia_drm.modeset=1"
        if self.c.bootloader == "systemd-boot":
            if self.c.mode == "auto":
                self.chroot("bootctl", "--esp-path=/boot", "install")
            else:
                destination = self.target / "boot/EFI/EasyArch/systemd-bootx64.efi"
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() or destination.is_symlink():
                    raise RuntimeError("EasyArch EFI path already exists")
                shutil.copyfile(
                    self.target / "usr/lib/systemd/boot/efi/systemd-bootx64.efi",
                    destination,
                )
                esp = next(
                    p for p in self.parts if p.role == "esp" and p.mountpoint == "/boot"
                )
                self.r.run(
                    [
                        "efibootmgr",
                        "--create",
                        "--disk",
                        self.c.device.path,
                        "--part",
                        str(esp.number),
                        "--label",
                        "Easy Arch",
                        "--loader",
                        r"\EFI\EasyArch\systemd-bootx64.efi",
                    ]
                )
            self.write(
                "boot/loader/loader.conf",
                f"default easy-arch-{self.c.kernels[0]}.conf\ntimeout 5\neditor no\n",
            )
            for kernel in self.c.kernels:
                self.write(
                    f"boot/loader/entries/easy-arch-{kernel}.conf",
                    f"title Arch Linux ({kernel})\nlinux /vmlinuz-{kernel}\ninitrd /initramfs-{kernel}.img\noptions {cmdline}\n",
                )
        else:
            self.write(
                "etc/default/grub",
                f'GRUB_DEFAULT=0\nGRUB_TIMEOUT=5\nGRUB_DISTRIBUTOR="Arch Linux"\nGRUB_CMDLINE_LINUX="{cmdline}"\nGRUB_DISABLE_OS_PROBER=true\n',
            )
            if self.c.firmware == "uefi":
                self.chroot(
                    "grub-install",
                    "--target=x86_64-efi",
                    "--efi-directory=/boot",
                    "--bootloader-id=EasyArch",
                )
                # Removable fallback is deliberately NOT written on an existing ESP.
                if self.c.mode == "auto":
                    self.chroot(
                        "grub-install",
                        "--target=x86_64-efi",
                        "--efi-directory=/boot",
                        "--removable",
                    )
            else:
                self.chroot("grub-install", "--target=i386-pc", self.c.device.path)
            self.chroot("grub-mkconfig", "-o", "/boot/grub/grub.cfg")

    def verify(self):
        for name in [
            "etc/fstab",
            "etc/passwd",
            "etc/locale.conf",
            "usr/bin/Hyprland"
            if self.c.desktop == "hyprland"
            else "usr/bin/startplasma-wayland",
        ]:
            if not (self.target / name).is_file():
                raise RuntimeError(f"Installed system verification failed: {name}")
        for kernel in self.c.kernels:
            for name in [f"vmlinuz-{kernel}", f"initramfs-{kernel}.img"]:
                if not (self.target / "boot" / name).is_file():
                    raise RuntimeError(f"Missing boot artifact: {name}")
        if self.c.bootloader == "grub":
            self.chroot("grub-script-check", "/boot/grub/grub.cfg")
        self.chroot("pacman", "-Q", *self.plan.packages)
        self.chroot("getent", "passwd", self.c.username)
        for service in self.services():
            self.r.run(["systemctl", "--root", str(self.target), "is-enabled", service])
        self.chroot("findmnt", "--verify", "--tab-file", "/etc/fstab")
        self.r.event(
            "verified",
            checks="files, kernels, initramfs, packages, user, fstab, enabled services",
            reboot_tested=False,
        )

    def finish(self):
        if self.r.journal_path:
            self.write(
                "var/log/easy-arch/install.jsonl",
                self.r.journal_path.read_text(),
                0o600,
            )
            self.write(
                "var/log/easy-arch/plan.json",
                json.dumps(self.plan.to_dict(), indent=2),
                0o600,
            )
        self.r.run(["sync"])
        self.r.run(["umount", "--recursive", str(self.target)])
        self.mounted = False
        if self.mapper_open:
            self.r.run(["cryptsetup", "close", self.mapper])
            self.mapper_open = False

    def cleanup(self):
        # No automatic retries of partitioning/formatting. Retain logs and report leaks.
        if self.mounted:
            try:
                self.r.run(["umount", "--recursive", str(self.target)], cleanup=True)
                self.mounted = False
            except Exception as exc:
                self.r.event("cleanup_error", message=str(exc))
        if self.mapper_open and not self.mounted:
            try:
                self.r.run(["cryptsetup", "close", self.mapper], cleanup=True)
                self.mapper_open = False
            except Exception as exc:
                self.r.event("cleanup_error", message=str(exc))
