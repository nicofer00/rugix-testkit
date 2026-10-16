"""Typed wrapper around the rugix-ctrl CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .result import CmdResult
from .types import JsonObject

if TYPE_CHECKING:
    from .qemu.handle import VMHandle


@dataclass
class SystemInfo:
    """Parsed output of ``rugix-ctrl system info --json``."""

    raw: JsonObject

    @property
    def boot_flow(self) -> str:
        """The active boot flow (e.g. ``"grub"``)."""
        boot = self.raw["boot"]
        assert isinstance(boot, dict)
        result = boot["bootFlow"]
        assert isinstance(result, str)
        return result

    @property
    def active_group(self) -> str | None:
        """The currently booted group (e.g. ``"a"``), or ``None``."""
        boot = self.raw["boot"]
        assert isinstance(boot, dict)
        result = boot.get("activeGroup")
        assert result is None or isinstance(result, str)
        return result

    @property
    def default_group(self) -> str | None:
        """The default boot group, or ``None``."""
        boot = self.raw["boot"]
        assert isinstance(boot, dict)
        result = boot.get("defaultGroup")
        assert result is None or isinstance(result, str)
        return result

    @property
    def slots(self) -> JsonObject:
        """Slot information keyed by slot name."""
        result = self.raw.get("slots", {})
        assert isinstance(result, dict)
        return result


class RugixCtrl:
    """Interface to rugix-ctrl running inside a VM."""

    def __init__(self, vm: VMHandle) -> None:
        self.vm = vm

    def system_info(self) -> SystemInfo:
        """Query system info from rugix-ctrl."""
        data = self.vm.run_json(["rugix-ctrl", "system", "info", "--json"])
        return SystemInfo(raw=data)

    def update_install(
        self,
        source: str,
        *,
        reboot: str = "no",
        insecure: bool = True,
        root_cert: str | None = None,
        check: bool = True,
        timeout: float = 300,
    ) -> CmdResult:
        """Install an update bundle from *source*.

        *source* is forwarded verbatim to ``rugix-ctrl``, typically a
        URL or a path on the VM. For a bundle that lives on the host,
        use :meth:`update_install_file`.
        """
        cmd = ["rugix-ctrl", "update", "install", "--reboot", reboot]
        cmd += _verification_args(insecure, root_cert)
        return self.vm.run([*cmd, source], hide=True, timeout=timeout, check=check)

    def update_install_file(
        self,
        local_bundle: Path,
        *,
        remote_path: str = "/tmp/rugix-update.rugixb",
        reboot: str = "no",
        insecure: bool = True,
        root_cert: str | None = None,
        check: bool = True,
        timeout: float = 600,
    ) -> CmdResult:
        """Upload *local_bundle* to the VM and install it from there.

        Uploads to *remote_path* via SFTP, then delegates to
        :meth:`update_install`.
        """
        self.vm.upload(local_bundle, remote_path)
        return self.update_install(
            remote_path,
            reboot=reboot,
            insecure=insecure,
            root_cert=root_cert,
            check=check,
            timeout=timeout,
        )

    def install_and_reboot(
        self,
        source: str,
        *,
        insecure: bool = True,
        root_cert: str | None = None,
        timeout: float = 600,
    ) -> None:
        """Install an update with ``--reboot yes`` and wait for the reboot."""
        cmd = ["rugix-ctrl", "update", "install", "--reboot", "yes"]
        cmd += _verification_args(insecure, root_cert)
        self.vm.run_disconnecting([*cmd, source], timeout=timeout)
        self.vm.wait_for_reboot()

    def system_commit(self) -> CmdResult:
        """Commit the current system state."""
        return self.vm.run(["rugix-ctrl", "system", "commit"], hide=True)

    def assert_boot(self, *, default: str, active: str) -> SystemInfo:
        """Assert the default and active boot groups. Returns the system info."""
        info = self.system_info()
        assert (info.default_group, info.active_group) == (default, active), (
            f"expected default/active group {default}/{active}, "
            f"got {info.default_group}/{info.active_group}"
        )
        return info


def _verification_args(insecure: bool, root_cert: str | None) -> list[str]:
    args = ["--insecure-skip-bundle-verification"] if insecure else []
    if root_cert is not None:
        args += ["--root-cert", root_cert]
    return args
