"""QEMU configuration for Rugix Bakery's ``generic-grub-efi`` images.

Also provides an swtpm-emulated TPM 2.0 for testing TPM-backed features.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from types import TracebackType

from .config import Drive, Pflash, VMConfig

logger = logging.getLogger(__name__)


def amd64_efi_config(
    image: Path, *, disk_size: str = "40G", tpm_socket: Path | None = None
) -> VMConfig:
    """Build a :class:`VMConfig` for booting an amd64 ``generic-grub-efi`` image.

    The VM boots a copy-on-write overlay of *image* grown to *disk_size*.
    *tpm_socket* attaches a TPM 2.0 device backed by a running
    :class:`SwtpmProcess`.

    Firmware paths come from ``RUGIX_OVMF_CODE_AMD64`` and
    ``RUGIX_OVMF_VARS_AMD64``. Without them, the firmware shipped next to
    ``qemu-system-x86_64`` is used, falling back to Debian's OVMF package.
    """
    code, vars_ = _ovmf_paths()
    extra_args = ["-device", "virtio-rng-pci"]
    if tpm_socket is not None:
        extra_args += [
            "-chardev",
            f"socket,id=chrtpm,path={tpm_socket}",
            "-tpmdev",
            "emulator,id=tpm0,chardev=chrtpm",
            "-device",
            "tpm-tis,tpmdev=tpm0",
        ]
    return VMConfig(
        arch="x86_64",
        memory=2048,
        smp=2,
        drives=[
            Drive(
                file=image,
                format="raw",
                interface="virtio",
                overlay=True,
                size=disk_size,
            ),
        ],
        pflash=[
            Pflash(file=code, format="raw", readonly=True),
            Pflash(file=vars_, format="raw", readonly=False),
        ],
        extra_args=extra_args,
    )


class SwtpmProcess:
    """An ephemeral swtpm 2.0 process exposing a Unix socket for QEMU.

    Use it as a context manager so that the TPM and its state are removed
    together with the VM.
    """

    def __init__(self) -> None:
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._socket_path: Path | None = None

    @property
    def socket_path(self) -> Path:
        """The control socket to pass to :func:`amd64_efi_config`."""
        assert self._socket_path is not None, "SwtpmProcess not started"
        return self._socket_path

    def __enter__(self) -> SwtpmProcess:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="rugix-swtpm-")
        root = Path(self._tmpdir.name)
        state_dir = root / "state"
        state_dir.mkdir()
        self._socket_path = root / "ctrl.sock"

        # Creating EK and platform certificates requires a system-wide
        # writable state directory, which rootless setups lack. The TPM works
        # without them.
        subprocess.run(
            ["swtpm_setup", "--tpm2", "--tpm-state", str(state_dir)],
            check=True,
            capture_output=True,
        )
        self._process = subprocess.Popen(
            [
                "swtpm",
                "socket",
                "--tpm2",
                "--tpmstate",
                f"dir={state_dir}",
                "--ctrl",
                f"type=unixio,path={self._socket_path}",
                "--flags",
                "startup-clear",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self._socket_path.exists():
                logger.info("swtpm: socket ready at %s", self._socket_path)
                return self
            if self._process.poll() is not None:
                assert self._process.stderr is not None
                stderr = self._process.stderr.read()
                raise RuntimeError(
                    f"swtpm exited before its socket appeared: {stderr!r}"
                )
            time.sleep(0.05)
        raise TimeoutError("swtpm socket did not appear within 5 s")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        self._process = None
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
            self._tmpdir = None


def _ovmf_paths() -> tuple[Path, Path]:
    code = _resolve_ovmf(
        env="RUGIX_OVMF_CODE_AMD64",
        qemu_share="edk2-x86_64-code.fd",
        system=("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_CODE.fd"),
    )
    vars_ = _resolve_ovmf(
        env="RUGIX_OVMF_VARS_AMD64",
        qemu_share="edk2-i386-vars.fd",
        system=("/usr/share/OVMF/OVMF_VARS_4M.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
    )
    return code, vars_


def _resolve_ovmf(*, env: str, qemu_share: str, system: tuple[str, ...]) -> Path:
    override = os.environ.get(env)
    if override:
        return Path(override)
    candidates = [Path(path) for path in system]
    qemu = shutil.which("qemu-system-x86_64")
    if qemu is not None:
        share = Path(qemu).resolve().parent.parent / "share" / "qemu"
        candidates.insert(0, share / qemu_share)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Could not locate OVMF firmware. Set {env} to the file path."
    )
