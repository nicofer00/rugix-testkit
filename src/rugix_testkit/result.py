"""Command result and error types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CmdResult:
    """Result of a command executed on the VM."""

    command: str
    stdout: str
    stderr: str
    return_code: int

    @property
    def ok(self) -> bool:
        """Whether the command exited successfully."""
        return self.return_code == 0

    def __str__(self) -> str:
        lines = [f"$ {self.command}  [exit={self.return_code}]"]
        if self.stdout:
            lines.append(self.stdout.rstrip("\n"))
        if self.stderr:
            lines.append("--- stderr ---")
            lines.append(self.stderr.rstrip("\n"))
        return "\n".join(lines)


class CmdError(Exception):
    """Raised when a command exits with a non-zero return code."""

    result: CmdResult

    def __init__(self, result: CmdResult) -> None:
        self.result = result
        super().__init__(f"command failed\n{result}")
