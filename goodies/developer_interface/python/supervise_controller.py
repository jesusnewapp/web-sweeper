#!/usr/bin/env python3
"""Keep the local Web Sweeper controller alive in a user session."""

from __future__ import annotations

import subprocess
import os
import signal
import sys
import time
from pathlib import Path


RESTART_DELAY_SECONDS = 2
STOP_TIMEOUT_SECONDS = 5


def controller_command(arguments: list[str]) -> list[str]:
    server = Path(__file__).resolve().with_name("server.py")
    return [sys.executable, "-u", str(server), *arguments]


def stop_child(child: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    """Stop the whole controller process group before the supervisor exits."""
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def main(arguments: list[str] | None = None) -> int:
    forwarded = list(sys.argv[1:] if arguments is None else arguments)
    stopping = False
    child: subprocess.Popen[bytes] | None = None

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        if child is not None:
            stop_child(child)

    previous_handlers = {
        signum: signal.signal(signum, request_stop)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        while True:
            child = subprocess.Popen(
                controller_command(forwarded),
                start_new_session=True,
            )
            try:
                return_code = child.wait()
            except KeyboardInterrupt:
                stop_child(child)
                return 130
            if stopping:
                return 0
            if return_code == 0:
                return 0
            time.sleep(RESTART_DELAY_SECONDS)
    finally:
        if child is not None and child.poll() is None:
            stop_child(child)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
