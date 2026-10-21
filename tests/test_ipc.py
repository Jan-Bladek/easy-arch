import json
import os
import threading

from arch_gui_installer.core.ipc import ControlChannel, read_secrets


def test_immediate_cancel_is_not_lost():
    read_fd, write_fd = os.pipe()
    cancel = threading.Event()
    try:
        os.write(
            write_fd,
            json.dumps({"user_password": "test-password"}).encode()
            + b'\n{"action":"cancel"}\n',
        )
        secrets = read_secrets(read_fd)
        assert secrets.user_password == "test-password"
        control = ControlChannel(read_fd, cancel)
        control.start()
        assert cancel.wait(2)
        control.close()
        assert not control.thread.is_alive()
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_shutdown_does_not_wait_for_pipe_eof():
    read_fd, write_fd = os.pipe()
    try:
        channel = ControlChannel(read_fd, threading.Event())
        channel.start()
        channel.close()
        assert not channel.thread.is_alive()
    finally:
        os.close(write_fd)
        os.close(read_fd)
