"""Rugix testing toolkit: QEMU VM management and a Rugix Ctrl interface."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("rugix-testkit")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

from rugix_testkit.bakery import BakeryBuilder, Parameters
from rugix_testkit.bundler import Bundler
from rugix_testkit.qemu.config import Drive, Pflash, VMConfig, find_free_port
from rugix_testkit.qemu.efi import SwtpmProcess, amd64_efi_config
from rugix_testkit.qemu.handle import VMHandle
from rugix_testkit.qemu.vm import QemuVM
from rugix_testkit.result import CmdError, CmdResult
from rugix_testkit.rugix import RugixCtrl, SystemInfo
from rugix_testkit.types import JsonObject, JsonValue

__all__ = [
    "BakeryBuilder",
    "Bundler",
    "CmdError",
    "CmdResult",
    "Drive",
    "Parameters",
    "Pflash",
    "QemuVM",
    "RugixCtrl",
    "SwtpmProcess",
    "SystemInfo",
    "VMConfig",
    "VMHandle",
    "JsonObject",
    "JsonValue",
    "amd64_efi_config",
    "find_free_port",
]
