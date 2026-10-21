"""Argument-only subprocess execution, durable events, cooperative cancellation."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path


class Cancelled(RuntimeError):
    pass


class CommandError(RuntimeError):
    pass


class Runner:
    def __init__(self, emit: Callable[[dict], None], journal: Path | None = None):
        self.emit_callback = emit
        self.cancelled = threading.Event()
        self.journal_path = journal
        self.journal = None
        if journal:
            journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.journal = os.fdopen(fd, "w")

    def event(self, kind: str, **values) -> None:
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": kind,
            **values,
        }
        if self.journal:
            self.journal.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.journal.flush()
            os.fsync(self.journal.fileno())
        self.emit_callback(entry)

    def check_cancel(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled(
                "Cancelled at a safe command boundary; disk changes are not rolled back"
            )

    def run(
        self,
        args: list[str],
        *,
        stdin: bytes | None = None,
        timeout: int = 3600,
        capture: bool = False,
        cleanup: bool = False,
        sensitive: bool = False,
        interruptible: bool = False,
    ) -> str:
        if not cleanup:
            self.check_cancel()
        if not args or any(not isinstance(a, str) or "\0" in a for a in args):
            raise ValueError("Invalid command arguments")
        self.event("command", argv=args)
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": "/root"},
        )
        # Secret payloads are deliberately small (passwords/key material); never emit them.
        if stdin is not None:
            try:
                proc.stdin.write(stdin)
                proc.stdin.close()
            except BrokenPipeError:
                pass
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        output = bytearray()
        deadline = time.monotonic() + timeout
        try:
            while selector.get_map():
                if interruptible:
                    self.check_cancel()
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Command timed out: {args[0]}")
                for key, _ in selector.select(0.25):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if capture:
                        output.extend(chunk)
                    if not sensitive:
                        self.event("output", text=chunk.decode(errors="replace"))
            code = proc.wait(timeout=10)
            if code:
                raise CommandError(f"{args[0]} exited with status {code}")
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            raise
        finally:
            selector.close()
            proc.stdout.close()
        return output.decode(errors="replace")

    def close(self):
        if self.journal:
            self.journal.close()
