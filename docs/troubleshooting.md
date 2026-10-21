# Diagnostics and recovery

The GUI's installation page shows JSON events and command output. Save it using **Save log**. Root-only durable journals live at `/var/log/easy-arch/*.jsonl` in the live environment. Export them before shutting down. They identify the exact failed stage/command/exit status; secret command output is intentionally suppressed.

- **Disk identity changed / in use**: rescan and review again. Close file-manager mounts and swap only after checking what uses them. Never force-unmount your installed host root to bypass protection.
- **Live environment required**: boot the generated ISO. Do not create the marker on a regular installed system.
- **Missing packages**: confirm official repositories and internet access. Dry-run uses local metadata, real installation synchronizes isolated official databases. A package/version-family mismatch stops before partitioning.
- **Pacman/network failure**: inspect its actual error, mirror/TLS/clock, DNS and free cache space. Do not delete an active pacman lock. Correct the cause and generate a new reviewed plan. No destructive stage is retried automatically.
- **Out of space**: root needs >=16 GiB in manual layouts, >=12 GiB free after mounting; `/boot` needs space for every kernel/initramfs. A preserved ESP must be >=1 GiB and must not contain colliding Arch kernel filenames. Prefer a separate suitable ESP over overwriting another system's boot files.
- **Unsupported Hyprland/Noctalia version**: profile version gates protect against mixing incompatible configuration APIs. Update the profile and run desktop boot tests before widening the gate.
- **Cancelled**: the current command completes first. Partition changes already performed remain. Cleanup attempts unmount and mapper close; if either fails the log reports it. Inspect `findmnt`/`lsblk` before manual recovery or reboot.
- **Boot failure**: use another Arch live ISO, identify devices with `lsblk -f`, unlock LUKS where needed, mount root/subvolumes and ESP, inspect fstab, boot entry UUIDs and initramfs. Keep the failed disk snapshot for diagnosis. The installer does not claim to recover arbitrary partial installations automatically.

Never post Wi-Fi keyfiles, shadow files, passwords or complete core dumps in bug reports. Include the credential-free plan, relevant journal entries and reproducible VM settings.

## Final unmount reports “target is busy”

If verification completed and only stage 9 fails, the installed files/boot artifacts have passed static checks, but cleanup has not completed. Do not rerun the installation or format again. Save the live journal. A busy filesystem can have a process using a file/current directory, an active swapfile or remaining mounts. Inspect:

```sh
sudo findmnt -R /mnt/easy-arch-target
sudo fuser -vm /mnt/easy-arch-target
sudo swapon --show=NAME,TYPE
```

Close the identified program normally; leave shells/chroots using the target (`cd /` in the live shell). After the blocker has released it, `sudo umount -R /mnt/easy-arch-target` can finish cleanup. If it still fails, retain the log for diagnosis. Do not use `fuser -k`, forced or lazy unmount as a blanket workaround. Encryption mappings must only be closed after all target mounts are gone.

The updated source reports this state separately and collects read-only mount/process/swap diagnostics. It keeps reboot disabled while cleanup failed and does not claim installation completion. Both the worker and child commands use `/` as their working directory. The existing alpha.1 release ISO predates this change.

One reported alpha.1 installation subsequently booted after the user powered off and removed the ISO. The holder was not captured, so its cause remains unknown; that report does not establish that forced shutdown is a safe general recovery procedure.
