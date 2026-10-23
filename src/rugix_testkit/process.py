"""Run subprocesses while streaming output to the logger."""

from __future__ import annotations

import logging
import os
import pty
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

# CSI / OSC sequences that interactive TTY tools emit for progress UIs.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[P^_].*?\x1b\\"  # PM / APC / ...
    r"|\x1b."  # other single-char escapes
)


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
    (for example, Bakery in a container) still emit lines live. Carriage
    returns overwrite the current line (progress bars), and ANSI cursor/color
    controls are stripped before logging so they cannot rewrite the terminal.
    When *log_file* is set, the same cleaned lines are written there. Raises
    ``CalledProcessError`` on a non-zero exit, with the captured output
    attached.
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
        pending = _consume(decoded, pending, lines, logger, prefix, file)
    if pending:
        _emit(pending, lines, logger, prefix, file)


def _consume(
    decoded: str,
    pending: str,
    lines: list[str],
    logger: logging.Logger,
    prefix: str,
    file: TextIO | None,
) -> str:
    """Fold PTY text into lines; ``\\r`` overwrites the current line buffer."""
    # PTY ONLCR turns "\n" into "\r\n". Combined with a real CRLF that becomes
    # "\r\r\n"; collapse repeatedly so only bare CR remains for progress bars.
    while "\r\n" in decoded:
        decoded = decoded.replace("\r\n", "\n")
    for char in decoded:
        if char == "\n":
            _emit(pending, lines, logger, prefix, file)
            pending = ""
        elif char == "\r":
            # Progress bars: return to start of line and overwrite.
            pending = ""
        else:
            pending += char
    return pending


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _emit(
    line: str,
    lines: list[str],
    logger: logging.Logger,
    prefix: str,
    file: TextIO | None,
) -> None:
    text = _strip_ansi(line).rstrip("\r\n")
    if not text:
        # Skip blank lines and control-only sequences (cursor moves, clears).
        return
    stored = text + "\n"
    lines.append(stored)
    logger.debug("%s%s", prefix, text)
    if file is not None:
        file.write(stored)
        file.flush()
