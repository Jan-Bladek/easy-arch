from __future__ import annotations

import argparse
import json
import os
import resource
import signal
import sys
import uuid
from pathlib import Path

from arch_gui_installer.core.config import Config
from arch_gui_installer.core.ipc import ControlChannel, read_secrets
from arch_gui_installer.hardware.detect import hardware
from arch_gui_installer.packages.catalog import Catalog, selected_packages
from arch_gui_installer.storage.plan import Plan, make_plan


def emit(event: dict):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def build_plan(config: Config) -> Plan:
    config.validate()
    hw = hardware()
    catalog = Catalog()
    return make_plan(config, catalog.expand(selected_packages(config, hw)), hw)


def main() -> int:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description="Easy Arch graphical installer")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read-only GUI or JSON plan; never applies changes",
    )
    parser.add_argument(
        "--config", type=Path, help="Configuration JSON, without passwords"
    )
    parser.add_argument(
        "--apply", type=Path, help="Reviewed plan JSON; privileged live backend only"
    )
    parser.add_argument("--approval", help="SHA256 of the exact reviewed plan")
    args = parser.parse_args()
    if args.dry_run and args.apply:
        parser.error("--dry-run and --apply are mutually exclusive")
    if args.apply:
        from arch_gui_installer.installation.engine import Engine
        from arch_gui_installer.installation.runner import Cancelled, Runner

        runner = None
        control_channel = None
        try:
            plan = Plan(**json.loads(args.apply.read_text()))
            # The privileged worker must not keep the destination busy through
            # an inherited current directory (including its own parent process).
            os.chdir("/")
            if plan.schema != 1 or plan.digest() != args.approval:
                raise ValueError("Unapproved plan/schema")
            config = Config.from_dict(plan.config)
            # Do not trust command/partition lists supplied by the unprivileged UI.
            rebuilt = build_plan(config)
            if rebuilt.digest() != plan.digest():
                raise ValueError(
                    "Plan differs from configuration/current hardware/catalog; review it again"
                )
            secrets = read_secrets(sys.stdin.fileno())
            runner = Runner(
                emit, Path("/var/log/easy-arch") / f"{uuid.uuid4().hex}.jsonl"
            )

            control_channel = ControlChannel(sys.stdin.fileno(), runner.cancelled)
            control_channel.start()
            signal.signal(signal.SIGTERM, lambda *_: runner.cancelled.set())
            signal.signal(signal.SIGINT, lambda *_: runner.cancelled.set())
            Engine(plan, secrets, runner).install(args.approval)
            return 0
        except Cancelled as exc:
            if runner:
                runner.event("cancelled", message=str(exc), rollback=False)
            return 130
        except Exception as exc:
            if runner:
                runner.event("error", message=str(exc), rollback=False)
            else:
                emit({"event": "error", "message": str(exc)})
            return 1
        finally:
            if control_channel:
                control_channel.close()
            if runner:
                runner.close()
    if args.config:
        try:
            plan = build_plan(Config.from_dict(json.loads(args.config.read_text())))
            emit(
                {
                    "dry_run": True,
                    "approval": plan.digest(),
                    "plan": plan.to_dict(),
                    "catalog": "local official sync databases; freshness rechecked before installation",
                }
            )
            return 0
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
    from arch_gui_installer.gui.wizard import launch

    return launch(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
