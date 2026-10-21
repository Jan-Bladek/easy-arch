#!/usr/bin/env bash
set -euo pipefail
if (( $# < 1 )); then echo "Usage: $0 installer.iso [uefi|bios] [disk.qcow2]" >&2; exit 1; fi
iso_file=$(realpath "$1")
firmware_mode=${2:-uefi}
disk_file=${3:-easy-arch-test.qcow2}
[[ -f "$disk_file" ]] || qemu-img create -f qcow2 "$disk_file" 40G
args=(-machine q35 -m 6G -smp 4 -device virtio-vga -drive "file=$disk_file,if=virtio,format=qcow2" -cdrom "$iso_file" -boot order=d -netdev user,id=net0 -device virtio-net-pci,netdev=net0)
if [[ -r /dev/kvm && -w /dev/kvm ]]; then args+=(-accel kvm -cpu host); else args+=(-accel tcg -cpu max); fi
if [[ "$firmware_mode" == uefi ]]; then
    ovmf_code=${OVMF_CODE:-/usr/share/edk2/x64/OVMF_CODE.4m.fd}
    ovmf_vars=${OVMF_VARS:-/usr/share/edk2/x64/OVMF_VARS.4m.fd}
    [[ -f "$disk_file.vars" ]] || cp "$ovmf_vars" "$disk_file.vars"
    args+=(-drive "if=pflash,format=raw,readonly=on,file=$ovmf_code" -drive "if=pflash,format=raw,file=$disk_file.vars")
elif [[ "$firmware_mode" != bios ]]; then echo 'Invalid firmware mode' >&2; exit 1; fi
exec qemu-system-x86_64 "${args[@]}"
