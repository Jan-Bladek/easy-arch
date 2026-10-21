"""Official repository database reader. Pacman owns dependency resolution and conflicts."""

from __future__ import annotations

import tarfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    description: str
    repository: str
    groups: tuple[str, ...] = ()
    size: int = 0


SOFTWARE = {
    "development": [
        "git",
        "base-devel",
        "python",
        "rust",
        "gcc",
        "clang",
        "cmake",
        "gdb",
    ],
    "networking": [
        "openssh",
        "bind",
        "traceroute",
        "tcpdump",
        "nmap",
        "wireguard-tools",
        "openvpn",
    ],
    "virtualization": [
        "qemu-desktop",
        "libvirt",
        "virt-manager",
        "dnsmasq",
        "edk2-ovmf",
    ],
    "desktop": ["firefox", "vlc", "mousepad", "file-roller", "evince"],
}
BASE = [
    "base",
    "linux-firmware",
    "sof-firmware",
    "alsa-firmware",
    "mkinitcpio",
    "sudo",
    "networkmanager",
    "pipewire",
    "pipewire-alsa",
    "pipewire-pulse",
    "wireplumber",
    "polkit",
    "rtkit",
    "upower",
    "mesa",
    "vulkan-icd-loader",
    "ttf-dejavu",
    "noto-fonts",
    "xdg-user-dirs",
]
DESKTOP = {
    "kde": [
        "plasma-desktop",
        "plasma-workspace",
        "sddm",
        "dolphin",
        "konsole",
        "systemsettings",
        "discover",
        "packagekit-qt6",
        "plasma-nm",
        "plasma-pa",
        "kscreen",
        "powerdevil",
        "bluedevil",
        "ark",
        "spectacle",
        "okular",
        "xdg-desktop-portal-kde",
        "qt6-wayland",
        "firefox",
    ],
    "hyprland": [
        "hyprland",
        "noctalia",
        "sddm",
        "xorg-xwayland",
        "xdg-desktop-portal",
        "xdg-desktop-portal-hyprland",
        "xdg-desktop-portal-gtk",
        "qt6-wayland",
        "qt6ct",
        "kitty",
        "thunar",
        "thunar-archive-plugin",
        "gvfs",
        "file-roller",
        "firefox",
        "wl-clipboard",
        "cliphist",
        "grim",
        "slurp",
        "hyprshot",
        "hyprlock",
        "hypridle",
        "brightnessctl",
        "playerctl",
        "upower",
        "power-profiles-daemon",
        "adwaita-icon-theme",
        "gnome-themes-extra",
        "wofi",
        "pavucontrol",
        "network-manager-applet",
        "bluez-utils",
    ],
}


SYSTEM_DATABASES = Path("/var/lib/pacman/sync")
BUNDLED_DATABASES = Path("/usr/share/easy-arch/catalog")


def default_database_directory() -> Path:
    for directory in (SYSTEM_DATABASES, BUNDLED_DATABASES):
        if all((directory / f"{repo}.db").is_file() for repo in ("core", "extra")):
            return directory
    raise RuntimeError(
        "Official core/extra package metadata is missing. Use an Easy Arch live ISO with its bundled catalog."
    )


class Catalog:
    def __init__(self, directory: Path | None = None):
        directory = directory if directory is not None else default_database_directory()
        self.directory = directory
        self.packages: dict[str, Package] = {}
        self.groups: dict[str, list[str]] = {}
        for repo in ("core", "extra", "multilib"):
            db = directory / f"{repo}.db"
            if not db.exists():
                continue
            with tarfile.open(db) as archive:
                for member in archive:
                    if not member.name.endswith("/desc"):
                        continue
                    stream = archive.extractfile(member)
                    if not stream:
                        continue
                    raw = stream.read().decode()
                    fields = {}
                    for item in raw.strip().split("\n\n"):
                        lines = item.splitlines()
                        if lines:
                            fields[lines[0].strip("%")] = lines[1:]
                    name = fields["NAME"][0]
                    package = Package(
                        name,
                        fields["VERSION"][0],
                        " ".join(fields.get("DESC", [])),
                        repo,
                        tuple(fields.get("GROUPS", [])),
                        int(fields.get("ISIZE", [0])[0]),
                    )
                    self.packages.setdefault(name, package)
                    for group in package.groups:
                        self.groups.setdefault(group, []).append(name)

    def search(self, query: str = "", group: str = "") -> list[Package]:
        words = query.lower().split()
        return sorted(
            (
                p
                for p in self.packages.values()
                if all(w in f"{p.name} {p.description}".lower() for w in words)
                and (not group or group in p.groups)
            ),
            key=lambda p: p.name,
        )

    def expand(self, names: list[str]) -> list[str]:
        result: set[str] = set()
        for name in names:
            if name in self.packages:
                result.add(name)
            elif name in self.groups:
                result.update(self.groups[name])
            else:
                raise ValueError(
                    f"Package/group unavailable in official repositories: {name}"
                )
        return sorted(result)


def selected_packages(config, hw: dict) -> list[str]:
    result = BASE + DESKTOP[config.desktop] + config.kernels + config.packages
    cpu = hw["cpu"] if config.cpu == "auto" else config.cpu
    if cpu in {"intel", "amd"}:
        result += [f"{cpu}-ucode"]
    for profile in config.software:
        result += SOFTWARE[profile]
    result += {"ext4": ["e2fsprogs"], "btrfs": ["btrfs-progs"], "xfs": ["xfsprogs"]}[
        config.filesystem
    ]
    if config.firmware == "uefi":
        result += ["dosfstools", "efibootmgr"]
    if config.bootloader == "grub":
        result += ["grub", "os-prober"]
    if config.encrypt:
        result += ["cryptsetup"]
    if config.zram:
        result += ["zram-generator"]
    if config.snapshots:
        result += ["snapper", "snap-pac"]
    if config.bluetooth:
        result += ["bluez", "bluez-utils"]
    if config.ssh:
        result += ["openssh"]
    if config.firewall:
        result += ["nftables"]
    gpus = " ".join(hw.get("gpus", []))
    if config.gpu == "nvidia-open":
        result += ["nvidia-open-dkms", "nvidia-utils", "egl-wayland"] + [
            k + "-headers" for k in config.kernels
        ]
    elif config.gpu == "auto" and "NVIDIA" in gpus:
        # Architecture cannot be inferred reliably from the vendor name. Nouveau is the
        # conservative default; the UI offers explicit Turing+ nvidia-open selection.
        result += ["mesa"]
    if "Intel" in gpus:
        result += ["vulkan-intel", "intel-media-driver"]
    if any(s in gpus for s in ("AMD", "ATI")):
        result += ["vulkan-radeon", "libva-mesa-driver"]
    return sorted(set(result))
