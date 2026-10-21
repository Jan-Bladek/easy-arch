# Test procedure

`QT_QPA_PLATFORM=offscreen pytest -q` covers pure validation/planning, command failures and cancellation, argument injection, journal secret suppression, package/group catalog behavior, target path safety and real Qt event-loop startup/shutdown.

A real installation test is performed only in an isolated QEMU/Proxmox VM. Use a dedicated blank 40 GiB disk, 6 GiB RAM, a virtual GPU, network and sound devices. Select UEFI/OVMF without Secure Boot or SeaBIOS as appropriate. Never expose host physical disks via passthrough.

Matrix: UEFI/KDE/ext4, UEFI/Hyprland+Noctalia/Btrfs, UEFI/LUKS2, BIOS/GRUB, auto/manual, existing ESP, extra packages, network unavailable, package unavailable, out of space, and dry-run. `scripts/vm-install-case.py` is an automation helper to run **inside** a disposable prepared Arch VM; it requires a separate unmounted disk with serial `EASYARCH-TEST-TARGET` and `EASYARCH_TEST_PASSWORD` in the environment. It does real formatting and real installation. The cloud harness must carry the live marker; do not use that setup on a normal computer.

For each successful installation record the plan, installer JSONL, QEMU arguments, package versions and disk hash/snapshot. Shut down the live environment, remove its boot disk/ISO, and boot only the installed disk:

1. Verify bootloader menu and correct kernel/root UUID. For encryption confirm a wrong passphrase fails and the correct passphrase unlocks.
2. Log in through SDDM as the created user; verify sudo and locked root behavior.
3. Confirm KDE Wayland or Hyprland with a running Noctalia process starts without manual configuration. Test terminal, browser, file manager, shortcuts, lock/unlock, screenshot, clipboard and scaling.
4. Check `systemctl --failed`, `systemctl is-active NetworkManager sddm`, selected services, DNS and HTTPS connectivity.
5. Check user units `pipewire`, `pipewire-pulse`, `wireplumber`; play sound through an emulated audio device. `wpctl status` alone does not prove audible output.
6. Compare `pacman -Q` to plan packages; verify kernels/initramfs, `findmnt --verify`, swap/zram, Btrfs subvolumes and `snapper list-configs` where selected.
7. Export actual results, not expected results, into `docs/test-results.md`.

Failure injection: disconnect the virtual NIC before preflight/download; request a nonexistent package; use an undersized ESP/full filesystem; stop a download server or inject a command failure. Verify no destructive stage starts when preflight/transaction fails. For failures after partitioning, verify that the UI reports the failing stage, that logs survive and that cleanup errors are visible. Never claim disk changes were rolled back.

## Proxmox

Upload the generated ISO into ISO storage. Create a VM with q35, VirtIO SCSI or VirtIO block storage, 2–4 CPUs, 6 GiB RAM, a new >=40 GiB disk and a bridged VirtIO network adapter. For UEFI select OVMF, add an EFI disk, disable pre-enrolled Secure Boot keys. For BIOS select SeaBIOS. Use SPICE/VirtIO GPU for the desktop, optionally add Intel HDA sound for audio checks. Boot the ISO using the console, install, eject it and set disk-first boot order. Snapshot the blank disk before each independent matrix entry. Record Proxmox/QEMU versions and test results. GPU passthrough and real NVIDIA hardware require separate qualification.

`vm-install-case.py --manual` prepares a separate ESP and data partition with sentinel files, installs onto the explicitly formatted root, and mounts the preserved partitions read-only afterward to compare the sentinels. `--encrypt` tests automatic LUKS2; `--firewall` enables the offline nftables configuration including the explicitly requested SSH rule.

`vm-fault-cases.py` runs in the disposable VM and checks actual disk-table dumps before/after: a separate network namespace with no network, a nonexistent official package, and a resolved transaction too large for the planned root. No package payloads for the oversized transaction are downloaded and a destructive stage is forbidden in the test harness.

After upgrading a cloud harness kernel, reboot it before testing modules such as `dm_crypt`. Updating its kernel package while continuing to run the old kernel removes the old module tree; this is not representative of a freshly booted matching archiso image. The installer preflight also rejects that missing-module situation before formatting.

`check-installed.py` (install the `vm` optional dependency) uses an actual password SSH login on a loopback forwarded QEMU port and records packages, active services, failed system units and HTTPS connectivity. It does not claim to verify graphical login. `inspect-vm.py` uses a local QMP socket for VM input/screenshots. With EGL/virgl, QMP screendump can report `no surface`; capture from the guest's compositor instead, without treating that QEMU capture limitation as an installer failure.
