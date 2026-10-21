# Executed test results

Executed on **2026-10-21**, using real Arch repository packages. These results qualify the scenarios below, not every supported configuration or physical machine. This is pre-release software.

| Check | Result | Evidence |
|---|---|---|
| Python unit/integration suite | PASS: 59 tests (current source; alpha.1 had 54) | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` |
| GitHub Actions | PASS on initial implementation commit | [Executed CI run](https://github.com/Jan-Bladek/easy-arch/actions/runs/39657931615) |
| Static checks / wheel | PASS | Ruff E4/E7/E9/F/I; compileall; bash syntax; wheel build |
| Qt dark/light, PL/EN, event-loop shutdown | PASS | Offscreen Qt 6.12; GUI screenshot in assets |
| CLI dry-run | PASS | Real hardware/catalog reads; exported operation plan; no apply process |
| UEFI + automatic ext4 + KDE + git | PASS: fresh installation, reboot, password login, graphical Wayland login | [Install stages](evidence/kde-final-installation.json), [boot checks](evidence/kde-final-boot-report.json) |
| UEFI + manual Btrfs + Hyprland/Noctalia | PASS: installation, reboot, graphical login, autostart | [Install/preservation](evidence/hypr-installation.json), [boot checks](evidence/hypr-login-report.json), screenshot |
| Existing ESP and separate data partition | PASS: sentinel files preserved on both partitions | Same manual Btrfs installation; preservation assertion after unmount |
| Btrfs snapshots | PASS: timeline and explicit snapshot | [Snapper output](evidence/hypr-snapshots.txt) |
| BIOS + GPT + GRUB + KDE | PASS: installation, SeaBIOS boot, password and graphical login | [Install stages](evidence/bios-installation.json), [boot checks](evidence/bios-boot-report.json) |
| UEFI + LUKS2 Argon2id + KDE | PASS: public CLI apply, initramfs prompt, incorrect key rejected, correct key unlocks, graphical login | [Install stages](evidence/luks-installation.json), [boot checks](evidence/luks-boot-report.json), prompt screenshot |
| Encrypted system firewall | PASS: nftables input-drop rules loaded; SSH permitted | Inspected actual `nft list ruleset` after boot; nftables is a oneshot unit |
| Network and desktop services | PASS in all four installed scenarios | NetworkManager, HTTPS, SDDM; no failed system units; KWin/Plasma or Hyprland/Noctalia processes observed |
| Audio | PASS: virtual output playback/capture | PipeWire/WirePlumber active; generated 440 Hz PCM played with pw-play and recorded through QEMU HDA; [analysis](evidence/kde-audio-report.json) |
| Hyprland basic interaction | PASS | Terminal/file manager shortcuts, clipboard round trip, XWayland and hypridle observed; no Plasma packages installed |
| Network unavailable | PASS: fails before destructive stages | [VM fault cases](evidence/vm-fault-cases.json); network namespace without connectivity |
| Unavailable package | PASS: rejected before disk writes | Same VM fault report |
| Insufficient target capacity | PASS: rejected before disk writes | Same VM fault report; real package metadata and unchanged partition table |
| GUI → Polkit helper → root backend | PASS: typed serial, credential pipe, visible capacity failure, disk still blank | [Actual stage/error and lsblk](evidence/gui-helper-capacity.json); initial live image with session/mode fixes applied in RAM |
| Live ISO build and fresh UEFI boot | PASS: automatic live login and installer startup, disk/catalog detection | [Image checksum and exact scope](evidence/iso-verification.json), [screenshot](../assets/live-iso.png) |
| Busy final unmount diagnostics | PASS: real busy tmpfs mount, holder PID reported, holder left running; normal unmount after holder exits | [QEMU evidence](evidence/busy-unmount.json); controlled reproduction, original user blocker unknown; source update after alpha.1 |
| Fresh 0.1.1a1 ISO build and UEFI boot | PASS: versioned GUI autostart, disk detection, 15,521 package records; five embedded source modules match Git | [Release evidence](evidence/iso-0.1.1a1.json), [package versions](evidence/iso-0.1.1a1-packages.txt), [screenshot](../assets/live-iso-0.1.1a1.png); full install matrix not repeated |
| Physical installation, Wi-Fi/Bluetooth, microphones, NVIDIA, HiDPI/multi-monitor | NOT EXECUTED | No pass is claimed |
| XFS, PBKDF2, other kernels, BIOS encryption, UEFI GRUB, Secure Boot | NOT QUALIFIED by these VM runs | Some paths implemented; Secure Boot explicitly blocked |

## Environment and scope

QEMU q35/TCG, no `/dev/kvm`. Installation harnesses used 4 vCPUs and 4 GiB RAM; installed systems generally used 2 vCPUs and 4 GiB. UEFI used OVMF without Secure Boot. BIOS used SeaBIOS. Every target was an independent 32 GiB qcow2 virtual disk with an explicit serial. No host block devices were attached.

The disposable installation harness used the official Arch cloud image `20261015.611680`, SHA256 `08754e28d91b1680086c6211ef4df13559e766fff7cdb4006489b14b5d281f08`, checked against the official mirror checksum. This is not a separate signature-verification claim. Desktop testing used virtio-vga with Mesa/software rendering or EGL-headless through the host render node. Audio verification is a digital virtual-device test, not a claim about audible physical speakers.

All installed cases included the additional `git` package. Selected package versions: Linux `7.2.11.arch1-1`, Hyprland `0.56.2-4`, Noctalia `5.3.0-1`. The Hyprland case created a fresh Btrfs root while preserving an existing FAT ESP and an ext4 data partition. It used the guest's UEFI NVRAM, not a fabricated fallback boot entry.

Passwords used only for disposable VM accounts are not included in the evidence. Test-only SSH keys, disk images, complete package logs and cloud-init seed are excluded from Git. Public evidence contains compact status records and selected diagnostic output.

## Failures found and corrected

- An early offscreen smoke script exited while a QThread was still running. The core showed `QThread::~QThread` during QApplication teardown. The test now runs the event loop; shutdown waits for workers. Subsequent tests exit successfully.
- `genfstab` included the live environment's swapfile. The backend now removes inherited swap entries and writes only explicitly selected target swap. The first KDE test was repaired and rebooted for diagnosis; the table above uses a separate **fresh installation after the fix**.
- A cloud harness upgraded its kernel before rebooting, so the running kernel could not load `dm_crypt`. The failed LUKS attempt stopped on cryptsetup open. Module availability is now checked **before disk preparation**, and the harness was rebooted before the successful fresh encrypted installation.
- The initial live image had an incorrect LightDM session wrapper and archiso normalized the installer entry point to mode 0644. The source now specifies `/etc/lightdm/Xsession` and an explicit 0755 image permission. The live image also bundles official package metadata because archiso cleans pacman's sync databases.
- The default 256 MiB archiso writable overlay was insufficient for package downloads. The ISO now requests `cow_spacesize=75%`; the minimum recommended live VM memory is 6 GiB. Available RAM remains a practical limit for large package sets.

## Live image

See [ISO verification evidence](evidence/iso-verification.json) for the exact tested image checksum and boot result. The ISO is a generated artifact, excluded from Git. Its build scripts and complete profile overlay are included.

The four installation/reboot scenarios above ran through the real backend in disposable Arch harnesses. They do not imply that all four were repeated through the live ISO's wizard. GUI-to-helper and live boot checks are reported separately in the ISO evidence. A wider hardware and configuration campaign is still required before a stable release.
