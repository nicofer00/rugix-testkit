"""Run subprocesses while streaming output to the logger."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, TextIO


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

    When *log_file* is set, the same lines are written there. Raises
    ``CalledProcessError`` on a non-zero exit, with the captured output
    attached.
    """
    process = subprocess.Popen(
        list(cmd),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    lines: list[str] = []
    file: TextIO | None = None
    try:
        if log_file is not None:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file = log_file.open("w", encoding="utf-8")
        _stream_lines(process.stdout, lines, logger, prefix, file)
    finally:
        if file is not None:
            file.close()
        process.wait()

    output = "".join(lines)
    if process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode,
            list(cmd),
            output=output,
            stderr=None,
        )


def _stream_lines(
    stream: IO[str],
    lines: list[str],
    logger: logging.Logger,
    prefix: str,
    file: TextIO | None,
) -> None:
    pending = ""
    while True:
        chunk = stream.read(4096)
        if not chunk:
            break
        pending += chunk
        while True:
            newline = pending.find("\n")
            if newline < 0:
                break
            line = pending[: newline + 1]
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
    lines.append(line)
    logger.debug("%s%s", prefix, line.rstrip("\n"))
    if file is not None:
        file.write(line)
        file.flush()
