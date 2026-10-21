"""Normal unmount only, with read-only diagnostics on failure."""

from pathlib import Path
from shutil import which

from arch_gui_installer.installation.runner import CommandError, Runner


class UnmountError(CommandError):
    pass


def unmount(runner: Runner, target: Path, *, cleanup: bool = False) -> None:
    try:
        runner.run(["umount", "--recursive", str(target)], cleanup=cleanup)
    except CommandError as exc:
        # Never use lazy/forced unmount or kill all filesystem users. A busy
        # target needs inspection, not another installation/formatting attempt.
        if not cleanup:
            for command in [
                [
                    "findmnt",
                    "--submounts",
                    "--mountpoint",
                    str(target),
                    "--output",
                    "TARGET,SOURCE,FSTYPE",
                ],
                ["fuser", "-vm", str(target)],
                ["swapon", "--show=NAME,TYPE"],
            ]:
                if not which(command[0]):
                    runner.event("diagnostic_unavailable", tool=command[0])
                    continue
                try:
                    runner.run(command, timeout=15, cleanup=True)
                except Exception as diagnostic_error:
                    runner.event(
                        "diagnostic_error",
                        tool=command[0],
                        message=str(diagnostic_error),
                    )
        raise UnmountError(
            f"Could not unmount {target}. Close terminals/file managers using the "
            "target and inspect the mount/process diagnostics. Do not repeat "
            "partitioning or formatting; completed installation changes remain."
        ) from exc
