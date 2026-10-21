# Scope and current limitations

This is an initial implementation, not yet a production-qualified replacement for archinstall. Refer to the executed test report for acceptance criteria that remain open.

- x86_64 Arch Linux only, online installation, official signed core/extra/multilib packages.
- No automatic resizing, MBR manual editing, LVM/RAID, existing LUKS reuse, manual encrypted-root layout, hibernation/resume, Secure Boot signing or TPM enrollment. Unsupported combinations fail explicitly. No F2FS until qualified by actual installation tests.
- Manual mode requires an explicit row for every existing partition, a newly formatted root and an EFI partition >=1 GiB for UEFI. Existing small ESPs require another suitable ESP; resizing is not automated. Swap in manual mode uses zram. Formatting the ESP is an explicit destructive action; deleting an ESP is blocked even if its role is mislabeled.
- Btrfs subvolume policy is currently fixed; custom subvolume layouts and restoring snapshots from the boot menu are not provided. Snapshot creation is not a backup.
- Hardware detection identifies CPU vendor, PCI GPU/network/audio devices, network interfaces, battery presence and disk metadata. OS candidates are filesystem/type hints rather than a guaranteed inventory of every installed OS. HiDPI uses Qt's platform scaling and Hyprland automatic monitor scaling. No per-model NVIDIA compatibility database or proprietary legacy NVIDIA stack is included. Manually selecting nvidia-open requires compatible Turing-or-newer hardware.
- The main interface has Polish/English text; low-level operation names, diagnostics, package descriptions and some technical tooltips retain English or bilingual text. The implemented eleven pages consolidate the proposed eighteen-step flow.
- Packages are filtered by pacman groups and software profiles; search results display up to 500 entries at a time. Dependency/conflict diagnostics appear in transaction logs before disk modification. No AUR.
- NetworkManager's graphical connection editor handles live Wi-Fi/Ethernet/VPN configuration. Copying live profiles is opt-in because those keyfiles include Wi-Fi credentials. Supplementary groups are selected from a validated set. No standalone installer Wi-Fi editor or arbitrary systemd-service editor yet.
- Repository downloads can fail or packages can change. Failures are visible, without an unattended reinstall/reformat retry. The package catalog shown in dry-run reflects the local sync DB; real installation rechecks it against synchronized official repos.
- The ISO build process can be repeated from recorded packages and source, but rolling mirrors and current-date ISO metadata prevent a promise of byte-identical builds. A frozen signed repository snapshot is required for bitwise reproducibility.
- GUI local testing and backend VM testing are distinct from booting the generated live ISO on physical machines. Do not interpret unexecuted matrix entries as passes.

The live GUI initially shows the metadata snapshot recorded when the ISO was built (or the system's existing official sync DBs when available). Newly introduced package names/groups may require a newer ISO. No silent root-level repository refresh occurs during dry-run. The real transaction uses freshly synchronized official metadata and stops if selected names/version families are no longer compatible.
