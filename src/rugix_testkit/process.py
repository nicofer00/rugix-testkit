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

# Once the child uses in-place TTY updates, hold this many trailing lines so
# cursor-up / clear-line redraws replace them instead of duplicating logs.
_SOFT_TAIL = 16

_CSI_FINAL = re.compile(r"\x1b\[([0-9;?]*)([@-~])")


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

    The child runs on a PTY so line-buffered tools still stream live. A virtual
    screen interprets ``\\r``, cursor-up, and erase sequences like a terminal:

    - The latest line is always held briefly so a carriage-return progress
      update can replace it.
    - After the first in-place control (cursor-up / erase), a larger soft tail
      is held so bakery-style redraws (for example, layer status blocks) update
      in place instead of flooding the log with duplicates.

    Held lines are flushed when they scroll out of the soft window, or when the
    process exits. When *log_file* is set, the final cleaned lines are written
    there. Raises ``CalledProcessError`` on a non-zero exit.
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

    screen = _Screen(logger, prefix)
    file: TextIO | None = None
    try:
        if log_file is not None:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file = log_file.open("w", encoding="utf-8")
            screen.log_file = file
        _stream_pty(master, screen)
        screen.flush_all()
    finally:
        if file is not None:
            file.close()
        os.close(master)
        process.wait()

    output = "".join(
        f"{line}\n" for line in screen.lines if line.rstrip("\r\n")
    )
    if process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode,
            list(cmd),
            output=output,
            stderr=None,
        )


def _stream_pty(master: int, screen: _Screen) -> None:
    while True:
        try:
            data = os.read(master, 4096)
        except OSError:
            data = b""
        if not data:
            break
        screen.feed(data.decode("utf-8", errors="replace"))


class _Screen:
    """Virtual screen that turns TTY in-place updates into stable log lines."""

    def __init__(self, logger: logging.Logger, prefix: str) -> None:
        self.logger = logger
        self.prefix = prefix
        self.log_file: TextIO | None = None
        self.lines: list[str] = []
        self.row = 0
        self.col = 0
        self.emitted = 0
        self.max_row = 0
        self.max_cursor_up = 0
        self.interactive = False

    def feed(self, text: str) -> None:
        while "\r\n" in text:
            text = text.replace("\r\n", "\n")
        i = 0
        while i < len(text):
            if text[i] == "\x1b":
                i = self._feed_escape(text, i)
                continue
            char = text[i]
            i += 1
            if char == "\n":
                self._newline()
            elif char == "\r":
                self.col = 0
            elif char == "\b":
                self.col = max(0, self.col - 1)
            elif char in "\x0e\x0f":
                continue
            else:
                self._put(char)

    def _feed_escape(self, text: str, i: int) -> int:
        if i + 1 >= len(text):
            return len(text)
        if text[i + 1] != "[":
            return i + 2
        match = _CSI_FINAL.match(text, i)
        if match is None:
            return i + 2
        self._csi(match.group(1), match.group(2))
        return match.end()

    def _csi(self, params: str, final: str) -> None:
        parts = [p for p in params.split(";") if p]
        nums = [int(p) if p.isdigit() else 0 for p in parts] or [0]
        n = nums[0] or 1
        if final == "A":
            self.interactive = True
            self.max_cursor_up = max(self.max_cursor_up, n)
            self.row = max(0, self.row - n)
            self.col = 0
        elif final == "B":
            self.row += n
            self.col = 0
            self._ensure_row(self.row)
            self._bump_max()
        elif final == "C":
            self.col += n
        elif final == "D":
            self.col = max(0, self.col - n)
        elif final in "Hf":
            row = (nums[0] if nums else 1) or 1
            col = (nums[1] if len(nums) > 1 else 1) or 1
            self.row = max(0, row - 1)
            self.col = max(0, col - 1)
            self._ensure_row(self.row)
            self._bump_max()
        elif final == "K":
            self.interactive = True
            self._ensure_row(self.row)
            mode = nums[0] if params else 0
            line = self.lines[self.row]
            if mode == 0:
                self.lines[self.row] = line[: self.col]
            elif mode == 1:
                self.lines[self.row] = (" " * self.col) + line[self.col :]
            else:
                self.lines[self.row] = ""
                self.col = 0
        elif final == "J":
            self.interactive = True
            mode = nums[0] if params else 0
            if mode >= 2:
                self.lines.clear()
                self.row = 0
                self.col = 0
                self.max_row = 0
                self.emitted = 0
            elif mode == 0:
                self._ensure_row(self.row)
                self.lines[self.row] = self.lines[self.row][: self.col]
                del self.lines[self.row + 1 :]
                self.max_row = len(self.lines)

    def _put(self, char: str) -> None:
        self._ensure_row(self.row)
        line = self.lines[self.row]
        if self.col < len(line):
            line = line[: self.col] + char + line[self.col + 1 :]
        else:
            line = line + (" " * (self.col - len(line))) + char
        self.lines[self.row] = line
        self.col += 1
        self._bump_max()

    def _newline(self) -> None:
        self.row += 1
        self.col = 0
        self._ensure_row(self.row)
        self._bump_max()
        self._flush_stable()

    def _ensure_row(self, row: int) -> None:
        while len(self.lines) <= row:
            self.lines.append("")

    def _bump_max(self) -> None:
        self.max_row = max(self.max_row, self.row)

    def _soft(self) -> int:
        # Always hold the open line so CR progress can replace it. After the
        # child starts redrawing with cursor/erase controls, hold a larger tail.
        if self.interactive:
            return max(_SOFT_TAIL, self.max_cursor_up, 1)
        return 1

    def _commit_upto(self) -> int:
        return max(0, self.max_row - self._soft())

    def _flush_stable(self) -> None:
        limit = self._commit_upto()
        while self.emitted < limit and self.emitted < len(self.lines):
            self._emit_line(self.lines[self.emitted])
            self.emitted += 1

    def flush_all(self) -> None:
        while self.emitted < len(self.lines):
            self._emit_line(self.lines[self.emitted])
            self.emitted += 1

    def _emit_line(self, line: str) -> None:
        text = line.rstrip("\r\n")
        if not text:
            return
        self.logger.debug("%s%s", self.prefix, text)
        if self.log_file is not None:
            self.log_file.write(text + "\n")
            self.log_file.flush()
