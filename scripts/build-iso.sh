#!/usr/bin/env bash
# Build only in a disposable Arch builder/VM, not an installed desktop.
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if (( EUID != 0 )); then
    echo 'Run as root in an Arch Linux build VM with archiso, python-build, python-installer and python-setuptools.' >&2
    exit 1
fi
command -v mkarchiso >/dev/null
profile_dir="$repo_dir/iso/profile"
if [[ -e "$profile_dir" ]]; then
    echo "Profile already exists: $profile_dir. Move it aside to start a fresh build." >&2
    exit 1
fi
build_workspace=$(mktemp -d "$repo_dir/iso/build.XXXXXX")
trap 'rm -rf -- "$build_workspace"' EXIT
app_version=$(python -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["version"])' "$repo_dir/pyproject.toml")
python -m build --wheel --no-isolation --outdir "$build_workspace/wheels" "$repo_dir"
cp -a /usr/share/archiso/configs/releng "$profile_dir"
cat "$repo_dir/iso/packages.extra" >> "$profile_dir/packages.x86_64"
sort -u -o "$profile_dir/packages.x86_64" "$profile_dir/packages.x86_64"
cp -a "$repo_dir/iso/airootfs/." "$profile_dir/airootfs/"
# archiso removes /var/lib/pacman/sync while cleaning its image. Preserve a
# separate official metadata snapshot so the GUI/dry-run works on first boot.
catalog_dir="$profile_dir/airootfs/usr/share/easy-arch/catalog"
catalog_workspace="$build_workspace/catalog"
mkdir -p "$catalog_dir" "$catalog_workspace/db/local"
PYTHONPATH="$repo_dir/src" python -c 'from arch_gui_installer.installation.engine import PACMAN_CONFIG; print(PACMAN_CONFIG)' > "$catalog_workspace/pacman.conf"
pacman --config "$catalog_workspace/pacman.conf" --dbpath "$catalog_workspace/db" -Sy --noconfirm
for repository in core extra multilib; do
    cp -L "$catalog_workspace/db/sync/$repository.db" "$catalog_dir/"
done
date -u +%FT%TZ > "$catalog_dir/snapshot-date"
python -m installer --destdir="$profile_dir/airootfs" "$build_workspace"/wheels/*.whl
python - "$repo_dir" "$profile_dir/airootfs" "$app_version" <<'PY'
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

repo, root, version = sys.argv[1:]
try:
    commit = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
except (OSError, subprocess.CalledProcessError):
    commit = "unknown (source archive)"
destination = Path(root) / "usr/share/easy-arch/build-info.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps({"version": version, "source_commit": commit,
    "built_at_utc": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")
PY
chmod 0755 "$profile_dir/airootfs/usr/bin/easy-arch" "$profile_dir/airootfs/usr/lib/easy-arch/backend" "$profile_dir/airootfs/usr/local/bin/easy-arch-live-setup"
mkdir -p "$profile_dir/airootfs/etc/systemd/system/multi-user.target.wants"
ln -sf /usr/lib/systemd/system/NetworkManager.service "$profile_dir/airootfs/etc/systemd/system/multi-user.target.wants/NetworkManager.service"
ln -sf /etc/systemd/system/easy-arch-live-setup.service "$profile_dir/airootfs/etc/systemd/system/multi-user.target.wants/easy-arch-live-setup.service"
ln -sf /usr/lib/systemd/system/lightdm.service "$profile_dir/airootfs/etc/systemd/system/display-manager.service"
ln -sf /usr/lib/systemd/system/graphical.target "$profile_dir/airootfs/etc/systemd/system/default.target"
# Remove releng's competing network managers; only the live image is modified.
find "$profile_dir/airootfs/etc/systemd/system" -type l \( -name 'systemd-networkd*' -o -name 'iwd*' \) -delete
cat >> "$profile_dir/profiledef.sh" <<'PROFILE'
iso_name="easy-arch"
iso_publisher="Easy Arch project"
iso_application="Easy Arch graphical installer"
kernel_params_x86_64="cow_spacesize=75%"
airootfs_image_tool_options=('-comp' 'zstd' '-Xcompression-level' '6' '-b' '1M')
file_permissions+=(
  ["/usr/bin/easy-arch"]="0:0:755"
  ["/usr/lib/easy-arch/backend"]="0:0:755"
  ["/usr/local/bin/easy-arch-live-setup"]="0:0:755"
  ["/etc/polkit-1/rules.d/49-easy-arch-live.rules"]="0:0:644"
)
PROFILE
printf '\niso_version="%s"\n' "$app_version" >> "$profile_dir/profiledef.sh"
mkdir -p "$repo_dir/iso/out"
mkarchiso -v -w "$repo_dir/iso/work" -o "$repo_dir/iso/out" "$profile_dir"
(cd "$repo_dir/iso/out" && sha256sum "easy-arch-$app_version-x86_64.iso" > SHA256SUMS)
