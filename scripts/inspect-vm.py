#!/usr/bin/env python3
"""Inspect ONLY a QEMU instance exposed through an explicit local QMP socket.
Typing reads EASYARCH_VM_TEXT to avoid passwords in process arguments or logs.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
from pathlib import Path


class QMP:
    def __init__(self, path: str):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(path)
        self.stream = self.sock.makefile("rwb")
        json.loads(self.stream.readline())
        self.command("qmp_capabilities")

    def command(self, name, arguments=None):
        self.stream.write(
            json.dumps({"execute": name, "arguments": arguments or {}}).encode() + b"\n"
        )
        self.stream.flush()
        while True:
            result = json.loads(self.stream.readline())
            if "error" in result:
                raise RuntimeError(result["error"])
            if "return" in result:
                return result["return"]

    def key(self, keys):
        self.command(
            "send-key",
            {"keys": [{"type": "qcode", "data": key} for key in keys], "hold-time": 10},
        )
        # Leave time for key release in slow TCG guests before the next chord.
        time.sleep(0.25)


parser = argparse.ArgumentParser()
parser.add_argument("--qmp", required=True)
parser.add_argument("--screenshot", type=Path)
parser.add_argument(
    "--key", help="Comma-separated simultaneous QEMU qcode keys, e.g. ctrl,alt,f2"
)
parser.add_argument("--type-env", action="store_true")
parser.add_argument("--click", help="Absolute tablet coordinates x,y,width,height")
args = parser.parse_args()
qmp = QMP(args.qmp)
if args.click:
    x, y, width, height = map(int, args.click.split(","))
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError("Click outside screen")
    qmp.command(
        "input-send-event",
        {
            "events": [
                {"type": "abs", "data": {"axis": "x", "value": x * 32767 // width}},
                {"type": "abs", "data": {"axis": "y", "value": y * 32767 // height}},
                {"type": "btn", "data": {"button": "left", "down": True}},
            ]
        },
    )
    time.sleep(0.25)
    qmp.command(
        "input-send-event",
        {
            "events": [
                {"type": "btn", "data": {"button": "left", "down": False}},
            ]
        },
    )
    time.sleep(0.25)
if args.key:
    qmp.key(args.key.split(","))
if args.type_env:
    punctuation = {
        "\n": ["ret"],
        "\t": ["tab"],
        " ": ["spc"],
        "-": ["minus"],
        "_": ["shift", "minus"],
        ".": ["dot"],
        ":": ["shift", "semicolon"],
        "!": ["shift", "1"],
        "@": ["shift", "2"],
    }
    punctuation.update(
        {
            "/": ["slash"],
            "=": ["equal"],
            "'": ["apostrophe"],
            '"': ["shift", "apostrophe"],
            ">": ["shift", "dot"],
            "<": ["shift", "comma"],
            ";": ["semicolon"],
            "|": ["shift", "backslash"],
            "$": ["shift", "4"],
            "&": ["shift", "7"],
            "(": ["shift", "9"],
            ")": ["shift", "0"],
            "[": ["bracket_left"],
            "]": ["bracket_right"],
            "#": ["shift", "3"],
            "%": ["shift", "5"],
            "+": ["shift", "equal"],
            "?": ["shift", "slash"],
            ",": ["comma"],
            "\\": ["backslash"],
        }
    )
    for char in os.environ.pop("EASYARCH_VM_TEXT"):
        if char in punctuation:
            keys = punctuation[char]
        elif char.isascii() and char.isalnum():
            keys = ["shift", char.lower()] if char.isupper() else [char]
        else:
            raise ValueError("Unsupported test keyboard character")
        qmp.key(keys)
if args.screenshot:
    from PySide6.QtGui import QImage

    destination = args.screenshot.resolve()
    ppm = destination.with_suffix(".ppm")
    qmp.command("screendump", {"filename": str(ppm)})
    if not QImage(str(ppm)).save(str(destination)):
        raise RuntimeError("Could not encode screenshot")
    ppm.unlink()
    print(destination)
if not (args.screenshot or args.key or args.type_env or args.click):
    print(qmp.command("query-status"))
