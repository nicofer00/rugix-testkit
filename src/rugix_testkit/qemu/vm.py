"""QEMU virtual machine process management."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .config import VMConfig, find_free_port

logger = logging.getLogger(__name__)


class QemuVM:
    """
    Manages a QEMU virtual machine process.

    Call :meth:`prepare` to set up the work directory (overlays, firmware
    copies), then either :meth:`start` for programmatic use or :meth:`run`
    for an interactive session with the serial console on the terminal.
    """

    def __init__(
        self,
        config: VMConfig,
        *,
        work_dir: Path | None = None,
    ) -> None:
        self.config = config
        self._work_dir_provided = work_dir is not None
        self._work_dir = work_dir or Path(tempfile.mkdtemp(prefix="rugix-vm-"))
        self._process: subprocess.Popen[bytes] | None = None
        self._serial_log: list[str] = []
        self._serial_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def work_dir(self) -> Path:
        """The working directory for overlays and firmware copies."""
        return self._work_dir

    @property
    def serial_output(self) -> str:
        """Captured serial console output."""
        return "".join(self._serial_log)

    @property
    def is_running(self) -> bool:
        """Whether the QEMU process is currently running."""
        return self._process is not None and self._process.poll() is None

    def write_serial(self, data: bytes) -> None:
        """Write raw bytes to the VM's serial console."""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("QEMU process is not running")
        self._process.stdin.write(data)
        self._process.stdin.flush()

    def wait_for_serial(self, text: str, *, timeout: float = 30) -> str:
        """Wait until *text* appears in the captured serial output."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            output = self.serial_output
            if text in output:
                return output
            if self._process is not None and self._process.poll() is not None:
                break
            time.sleep(0.1)
        raise TimeoutError(f"serial output did not contain {text!r}")

    def prepare(self) -> None:
        """Create overlays and firmware copies in the work directory."""
        self._work_dir.mkdir(parents=True, exist_ok=True)

        for i, drive in enumerate(self.config.drives):
            if drive.overlay:
                if not drive.file.exists():
                    raise FileNotFoundError(f"Drive image not found: {drive.file}")
                overlay = self._work_dir / f"drive{i}.qcow2"
                cmd = [
                    "qemu-img",
                    "create",
                    "-f",
                    "qcow2",
                    "-b",
                    str(drive.file),
                    "-F",
                    drive.format,
                    str(overlay),
                ]
                if drive.size:
                    cmd.append(drive.size)
                subprocess.run(cmd, check=True, capture_output=True)

        for i, pf in enumerate(self.config.pflash):
            dst = self._work_dir / f"pflash{i}.{pf.format}"
            if pf.size:
                _create_sized_image(dst, pf.size, pf.file)
            elif not pf.readonly:
                if pf.file is None or not pf.file.exists():
                    raise FileNotFoundError(f"Pflash image not found: {pf.file}")
                shutil.copy2(pf.file, dst)
                # ``copy2`` preserves source mode; force writable so that
                # read-only firmware sources (e.g. nix-built OVMF) work.
                dst.chmod(dst.stat().st_mode | stat.S_IWUSR)

    def run(self) -> int:
        """
        Run QEMU interactively with the terminal attached.

        Returns the QEMU exit code.
        """
        ssh_port = self.config.ssh_port or find_free_port()
        cmd = self._build_cmd(ssh_port, interactive=True)
        logger.info("Running QEMU (interactive): %s", " ".join(cmd))
        logger.info("SSH available on port %d", ssh_port)
        result = subprocess.run(cmd)
        return result.returncode

    def start(self) -> int:
        """
        Start the QEMU process in the background.

        Returns the SSH port.
        """
        ssh_port = self.config.ssh_port or find_free_port()
        cmd = self._build_cmd(ssh_port, interactive=False)

        logger.info("Starting QEMU: %s", " ".join(cmd))

        self._stop_event.clear()
        self._serial_log.clear()
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        self._serial_thread = threading.Thread(target=self._read_serial, daemon=True)
        self._serial_thread.start()

        return ssh_port

    def stop(self, timeout: float = 15) -> None:
        """Stop the QEMU process."""
        if self._process is None:
            return

        self._stop_event.set()

        # Ctrl-A X is the QEMU monitor shortcut for immediate exit.
        try:
            if self._process.stdin:
                self._process.stdin.write(b"\x01x")
                self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("QEMU did not exit gracefully, killing.")
            self._process.kill()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.error("QEMU process did not terminate after kill.")

        self._process = None

    def cleanup(self) -> None:
        """Remove the work directory (only if auto-created)."""
        if not self._work_dir_provided and self._work_dir.exists():
            shutil.rmtree(self._work_dir, ignore_errors=True)

    def _build_cmd(self, ssh_port: int, interactive: bool) -> list[str]:
        cfg = self.config
        cmd = [
            cfg.qemu_binary,
            "-machine",
            cfg.effective_machine,
            "-smp",
            str(cfg.smp),
            "-m",
            str(cfg.memory),
            "-nographic",
        ]

        if not interactive:
            cmd.extend(["-serial", "mon:stdio"])

        if _kvm_available(cfg.arch):
            logger.info("KVM available, enabling hardware acceleration.")
            cmd.extend(["-enable-kvm", "-cpu", "host"])
        elif cfg.cpu:
            cmd.extend(["-cpu", cfg.cpu])

        for i, pf in enumerate(cfg.pflash):
            in_work_dir = pf.size is not None or not pf.readonly
            if in_work_dir:
                src = str(self._work_dir / f"pflash{i}.{pf.format}")
            else:
                src = str(pf.file)
            drive_str = f"if=pflash,format={pf.format},file={src}"
            if pf.readonly and not pf.size:
                drive_str += ",readonly=on"
            cmd.extend(["-drive", drive_str])

        for i, drive in enumerate(cfg.drives):
            if drive.overlay:
                path = str(self._work_dir / f"drive{i}.qcow2")
                fmt = "qcow2"
            else:
                path = str(drive.file)
                fmt = drive.format
            drive_str = f"format={fmt},file={path}"
            if drive.interface:
                drive_str = f"if={drive.interface},{drive_str}"
            cmd.extend(["-drive", drive_str])

        net_str = cfg.net
        if "hostfwd=" not in net_str:
            net_str += f",hostfwd=tcp:127.0.0.1:{ssh_port}-:22"
        cmd.extend(["-nic", net_str])

        cmd.extend(cfg.extra_args)
        return cmd

    def _read_serial(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        fd = self._process.stdout.fileno()
        try:
            while not self._stop_event.is_set():
                # Use `os.read` to bypass Python file buffering. Serial prompts
                # may not end in a newline and need to be captured promptly.
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                decoded = chunk.decode("utf-8", errors="replace")
                self._serial_log.append(decoded)
                logger.debug("serial: %r", decoded)
        except (ValueError, OSError):
            pass


_ARCH_TO_HOST = {"x86_64": "x86_64", "aarch64": "aarch64"}

_SIZE_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def _parse_size(size: str) -> int:
    """Parse a human-readable size string (e.g. '64M') to bytes."""
    suffix = size[-1].upper()
    if suffix in _SIZE_UNITS:
        return int(size[:-1]) * _SIZE_UNITS[suffix]
    return int(size)


def _create_sized_image(dst: Path, size: str, source: Path | None) -> None:
    """Create a zero-filled image of *size*, with *source* written at offset 0."""
    nbytes = _parse_size(size)
    with open(dst, "wb") as f:
        f.truncate(nbytes)
        if source is not None:
            data = source.read_bytes()
            f.seek(0)
            f.write(data)


def _kvm_available(arch: str) -> bool:
    host = _ARCH_TO_HOST.get(arch)
    return (
        host is not None
        and platform.machine() == host
        and os.access("/dev/kvm", os.W_OK)
    )
