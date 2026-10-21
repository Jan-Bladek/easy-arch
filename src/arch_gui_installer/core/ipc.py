"""A bounded credential message followed by cooperative control messages.

Use raw fd reads: a buffered stdin reader in a daemon thread can deadlock Python
shutdown, and buffered prefetch can swallow an immediately queued cancellation.
"""

from __future__ import annotations

import json
import os
import select
import threading

from arch_gui_installer.core.config import Secrets


def read_secrets(fd: int) -> Secrets:
    line = bytearray()
    while len(line) < 65536:
        byte = os.read(fd, 1)
        if byte == b"\n":
            return Secrets(**json.loads(line))
        if not byte:
            raise ValueError("Credential channel closed before a complete message")
        line.extend(byte)
    raise ValueError("Credential message exceeds its size limit")


class ControlChannel:
    def __init__(self, fd: int, cancelled: threading.Event):
        self.fd = fd
        self.cancelled = cancelled
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        pending = b""
        while not self.stop.is_set():
            if not select.select([self.fd], [], [], 0.2)[0]:
                continue
            data = os.read(self.fd, 4096)
            if not data:
                return
            pending += data
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                try:
                    if json.loads(line) == {"action": "cancel"}:
                        self.cancelled.set()
                except (ValueError, UnicodeError):
                    pass
            if len(pending) > 4096:
                return

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
