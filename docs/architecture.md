# Architecture

`src/arch_gui_installer` contains:

| Module | Responsibility |
|---|---|
| `gui/wizard.py` | Qt6 widgets, translations, validation, background detection, QProcess event stream |
| `core/config.py` | Typed intent, cross-field constraints, separate transient Secrets |
| `hardware/detect.py` | lsblk JSON and sysfs/PCI detection; device identity and mount checks |
| `storage/plan.py` | Pure layouts, explicit operations and SHA256 approval binding |
| `packages/catalog.py` | Read-only official sync DB catalog, groups, package/desktop profiles |
| `installation/runner.py` | Argument-only subprocesses, exit handling, timeouts, cancellation and journal |
| `installation/engine.py` | Preflight, transaction, partitioning, formatting, mount, pacstrap, system and boot setup |
| `profiles/` | Current Hyprland Lua, lock and idle configuration |
| `main.py` | GUI entry, read-only plan CLI and privileged live-only backend |

Qt stays on the main thread. Hardware/catalog work uses QThread and signals. Installation runs in a separate process with JSON events through QProcess. Backend stderr and stdout are visible in the log pane. No `shell=True` is used. Password inputs have bounded lengths and enter `chpasswd`/`cryptsetup` through pipes; output from secret-handling commands is suppressed, including errors that might echo input. Core dumps are disabled by the application entry point. Python cannot guarantee zeroization of immutable strings; plaintext secret files are never written by the installer.

The GUI creates a credential-free Plan. The user sees destructive actions and confirms the actual serial/WWN (path only as a final fallback for devices without those identifiers). The backend checks the exact digest and reconstructs the plan from config, current catalog and hardware. It checks the live marker, firmware, dependencies, mounts and full device identity (path, major/minor, capacity, model, serial, WWN, by-id). Immediately before the first write it repeats identity/mount checks. Manual partitions are revalidated against sfdisk GPT UUIDs/geometry. The host's mounted root/ancestors are rejected.

The live image runs the GUI as `live`. A root-owned dedicated pkexec helper runs Python isolated from current-directory/PYTHONPATH imports. Only the live image grants the active local `live` user that dedicated action. The installed target does not receive that policy or the live marker. Desktop policies do not grant an ordinary installed system permission to run the installer.

Stages emit start/end time, status, duration and command output to the GUI and root-only JSONL journals. Progress indicates completed stages rather than invented byte percentages. Failures propagate. There is no automatic retry of destructive steps. Cancellation sets a flag; current commands finish or reach their timeout before the next command is refused. Cleanup attempts recursive unmount and closes only the mapper it opened; errors remain visible. No automatic rollback is promised.

Target writes reject symlinks in path ancestors. The target root must be freshly formatted, the staging directory must be empty and no concurrent installer may use it. Existing ESP kernel filename collisions are rejected. Manual systemd-boot uses its own EFI executable path and NVRAM entry; existing OS loader files are not deleted. The shared loader configuration may change the default entry. Back up shared ESP data separately.

Cancellation from the unprivileged GUI travels through the backend's stdin control channel (a dedicated reader thread), not through a signal to a root-owned process. Signals are additionally handled for root/CLI administrators. The GUI keeps that channel open after its initial credential message. Closing the window while work is active is blocked.

Before partition writes, the live kernel is checked for required filesystem/device-mapper modules. An unmounted disk with an active crypt/LVM/RAID mapping is still treated as in use. Package transactions are resolved against an empty local DB, their installed sizes are compared with planned root capacity, and downloading can be cancelled before any disk write. `genfstab` output is filtered to remove swaps belonging to the live environment; only explicitly configured target swap is appended. Config writes are atomic, preserve existing hardlinks' other names, and retain backup copies of replaced files on preserved partitions. Successful installations keep a copy of the plan/journal under `/var/log/easy-arch/` in the target.
