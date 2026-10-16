"""High-level VM handle combining QEMU process and SSH connection."""

from __future__ import annotations

import json
import logging
import time
import warnings
from pathlib import Path
from typing import Any

import fabric
import paramiko
from invoke.exceptions import CommandTimedOut, ThreadException

from ..result import CmdError, CmdResult
from ..types import JsonObject
from .config import VMConfig
from .ssh import run_cmd, wait_for_ssh
from .vm import QemuVM

logger = logging.getLogger(__name__)


class VMHandle:
    """
    Convenience facade: running QEMU VM + SSH connection.

    Use as a context manager for automatic cleanup::

        with VMHandle.start(config) as vm:
            result = vm.run(["uname", "-a"])
    """

    def __init__(
        self,
        config: VMConfig,
        *,
        work_dir: Path | None = None,
        private_key: Path | None = None,
    ) -> None:
        self._qemu = QemuVM(config, work_dir=work_dir)
        self._private_key = private_key
        self._conn: fabric.Connection | None = None
        self._ssh_port: int | None = None
        self._boot_id: str | None = None
        self._command_history: list[CmdResult] = []
        self._closed = False

    @property
    def config(self) -> VMConfig:
        """The VM configuration."""
        return self._qemu.config

    @property
    def serial_output(self) -> str:
        """Captured serial console output from QEMU."""
        return self._qemu.serial_output

    def write_serial(self, data: bytes) -> None:
        """Write raw bytes to the VM's serial console."""
        self._qemu.write_serial(data)

    def wait_for_serial(self, text: str, *, timeout: float = 30) -> str:
        """Wait until *text* appears in the captured serial output."""
        return self._qemu.wait_for_serial(text, timeout=timeout)

    @classmethod
    def start(
        cls,
        config: VMConfig,
        *,
        work_dir: Path | None = None,
        boot_timeout: float = 300,
        private_key: Path | None = None,
    ) -> VMHandle:
        """
        Prepare a VM, start it, and wait for SSH.

        Args:
            config: VM configuration.
            work_dir: Persistent work directory (temp dir if *None*).
            boot_timeout: Seconds to wait for SSH after boot.
            private_key: Private-key file for public-key SSH auth. When
                omitted, falls back to passwordless ``auth_none``.
        """
        handle = cls(config, work_dir=work_dir, private_key=private_key)
        handle._qemu.prepare()
        handle._ssh_port = handle._qemu.start()

        try:
            handle._conn = wait_for_ssh(
                port=handle._ssh_port,
                timeout=boot_timeout,
                private_key=private_key,
            )
            handle._boot_id = _boot_id(handle._conn)
        except Exception:
            logger.error("Boot failed. Serial output:\n%s", handle._qemu.serial_output)
            handle._qemu.stop()
            handle._qemu.cleanup()
            raise

        return handle

    @property
    def command_history(self) -> list[CmdResult]:
        """All commands executed on the VM via :meth:`run`, in order."""
        return list(self._command_history)

    def run(self, args: list[str], *, check: bool = True, **kwargs: Any) -> CmdResult:
        """Run a command on the VM over SSH."""
        assert self._conn is not None
        result = run_cmd(self._conn, args, check=False, **kwargs)
        self._command_history.append(result)
        if check and not result.ok:
            raise CmdError(result)
        return result

    def run_json(self, args: list[str], **kwargs: Any) -> JsonObject:
        """Run a command on the VM and parse stdout as JSON."""
        result = self.run(args, hide=True, **kwargs)
        data: JsonObject = json.loads(result.stdout)
        return data

    def upload(self, local: Path, remote: str) -> None:
        """Copy *local* to *remote* on the VM via SFTP."""
        assert self._conn is not None
        self._conn.put(str(local), remote=remote)

    def close(self) -> None:
        """Close SSH, stop QEMU, and clean up the work directory."""
        self._closed = True
        if self._conn is not None:
            self._conn.close()
        self._qemu.stop()
        self._qemu.cleanup()

    def reboot(self, timeout: float = 300) -> None:
        """Reboot the VM and wait for SSH to come back up."""
        self.run_disconnecting(["reboot"])
        self.wait_for_reboot(timeout=timeout)

    def run_disconnecting(self, args: list[str], **kwargs: Any) -> None:
        """Run a command that may drop the SSH connection, e.g., by rebooting.

        Fails if the command exits with a non-zero code before the connection
        drops.
        """
        try:
            result = self.run(args, check=False, **{"hide": True, **kwargs})
        except _DISCONNECT_ERRORS:
            return
        # Paramiko reports -1 when the connection closes without an exit code.
        if result.return_code not in (0, -1):
            raise CmdError(result)

    def wait_for_reboot(self, timeout: float = 300) -> None:
        """Wait for an initiated reboot to complete and reconnect SSH.

        The VM counts as rebooted once it reports a new kernel boot ID.
        """
        assert self._ssh_port is not None and self._conn is not None
        self._conn.close()
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            conn = wait_for_ssh(
                port=self._ssh_port,
                timeout=remaining,
                private_key=self._private_key,
            )
            boot_id: str | None
            try:
                boot_id = _boot_id(conn)
            except (*_DISCONNECT_ERRORS, CommandTimedOut):
                # The shutting-down system may drop or stall the connection.
                boot_id = self._boot_id
            if boot_id != self._boot_id:
                self._conn = conn
                self._boot_id = boot_id
                return
            conn.close()
            time.sleep(1)
        raise TimeoutError(f"VM did not reboot within {timeout}s")

    def __enter__(self) -> VMHandle:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        if not self._closed:
            warnings.warn(
                "VMHandle was garbage collected without close(). "
                "Use 'with VMHandle.start(...) as vm:' to ensure cleanup.",
                ResourceWarning,
                stacklevel=1,
            )


_DISCONNECT_ERRORS = (OSError, EOFError, paramiko.SSHException, ThreadException)
"""Errors raised when an SSH connection drops during a command."""


def _boot_id(conn: fabric.Connection) -> str:
    path = "/proc/sys/kernel/random/boot_id"
    return run_cmd(conn, ["cat", path], hide=True, timeout=10).stdout.strip()
