"""Run subprocesses while streaming output to the logger."""

from __future__ import annotations

import logging
import os
import pty
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO


def run_logged(
    cmd: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    logger: logging.Logger,
    prefix: str = "",
    log_file: Path | None = None,
) -> None:
    """Run *cmd*, streaming merged stdout/stderr line-by-line at DEBUG.

    The child runs on a PTY so tools that block-buffer when stdout is a pipe
    (for example, Bakery in a container) still emit lines live. When *log_file*
    is set, the same lines are written there. Raises ``CalledProcessError`` on a
    non-zero exit, with the captured output attached.
    """
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            list(cmd),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
        )
    finally:
        os.close(slave)

    lines: list[str] = []
    file: TextIO | None = None
    try:
        if log_file is not None:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file = log_file.open("w", encoding="utf-8")
        _stream_pty(master, lines, logger, prefix, file)
    finally:
        if file is not None:
            file.close()
        os.close(master)
        process.wait()

    output = "".join(lines)
    if process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode,
            list(cmd),
            output=output,
            stderr=None,
        )


def _stream_pty(
    master: int,
    lines: list[str],
    logger: logging.Logger,
    prefix: str,
    file: TextIO | None,
) -> None:
    pending = ""
    while True:
        try:
            data = os.read(master, 4096)
        except OSError:
            data = b""
        if not data:
            break
        decoded = data.decode("utf-8", errors="replace")
        # PTY ONLCR turns "\n" into "\r\n". Strip CRs after normalizing CRLF so
        # we do not invent blank lines from "\r\r\n".
        decoded = decoded.replace("\r\n", "\n").replace("\r", "")
        pending += decoded
        while True:
            newline = pending.find("\n")
            if newline < 0:
                break
            line = pending[:newline]
            pending = pending[newline + 1 :]
            _emit(line, lines, logger, prefix, file)
    if pending:
        _emit(pending, lines, logger, prefix, file)


def _emit(
    line: str,
    lines: list[str],
    logger: logging.Logger,
    prefix: str,
    file: TextIO | None,
) -> None:
    text = line.rstrip("\r\n")
    stored = text + "\n"
    lines.append(stored)
    logger.debug("%s%s", prefix, text)
    if file is not None:
        file.write(stored)
        file.flush()
