"""Run subprocesses while streaming output to the logger."""

from __future__ import annotations

import logging
import os
import pty
import re
import subprocess
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

# Strip CSI / OSC / other escapes. Cursor-up is not applied to the transcript;
# instead, repeated redraw lines are skipped via a recent-line window so bake
# steps stay once without TTY UI spam.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[P^_].*?\x1b\\"
    r"|\x1b."
)

# Bakery redraws a status viewport; identical lines within this window are
# treated as UI refresh, not new steps.
_DEDUP_WINDOW = 64


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

    The child runs on a PTY so line-buffered tools still stream live. Output is
    normalized for a permanent transcript:

    - ANSI sequences (including cursor-up / clear-line) are stripped.
    - Bare ``\\r`` overwrites the *current* line only (progress bars).
    - Completed lines go to ``logger.debug`` and optional *log_file*; repeats
      inside a short window (TTY status redraws) are skipped so bake.log keeps
      steps without spam.

    Raises ``CalledProcessError`` on a non-zero exit.
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
    recent: deque[str] = deque(maxlen=_DEDUP_WINDOW)
    while True:
        try:
            data = os.read(master, 4096)
        except OSError:
            data = b""
        if not data:
            break
        decoded = data.decode("utf-8", errors="replace")
        while "\r\n" in decoded:
            decoded = decoded.replace("\r\n", "\n")
        decoded = _ANSI_RE.sub("", decoded)
        for char in decoded:
            if char == "\n":
                _commit(pending, lines, recent, logger, prefix, file)
                pending = ""
            elif char == "\r":
                pending = ""
            else:
                pending += char
    if pending:
        _commit(pending, lines, recent, logger, prefix, file)


def _commit(
    line: str,
    lines: list[str],
    recent: deque[str],
    logger: logging.Logger,
    prefix: str,
    file: TextIO | None,
) -> None:
    text = line.rstrip("\r\n")
    if not text:
        return
    if text in recent:
        # Same line recently logged — typical of a TTY status redraw.
        return
    recent.append(text)
    stored = text + "\n"
    lines.append(stored)
    logger.debug("%s%s", prefix, text)
    if file is not None:
        file.write(stored)
        file.flush()
