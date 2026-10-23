"""Tests for saving and DEBUG-logging VM outputs after a test."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from rugix_testkit.pytest_plugin import _VMS, _save_vm_outputs


class _FakeQemu:
    def __init__(self, serial: str) -> None:
        self.serial_output = serial


def test_save_vm_outputs_writes_serial_and_debugs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class Config:
        def getoption(self, name: str) -> str:
            assert name == "--test-outputs-dir"
            return str(tmp_path)

    class Item:
        nodeid = "tests/test_example.py::test_boot"
        config = Config()
        stash: dict[Any, Any] = {}

    item = Item()
    item.stash[_VMS] = [_FakeQemu("hello serial\n")]
    with caplog.at_level(logging.DEBUG, logger="rugix_testkit.pytest_plugin"):
        _save_vm_outputs(item)  # type: ignore[arg-type]

    serial_path = tmp_path / "tests--test_example.py--test_boot" / "serial.log"
    assert serial_path.read_text() == "hello serial\n"
    assert "hello serial" in caplog.text


def test_save_vm_outputs_noop_without_vms(tmp_path: Path) -> None:
    class Config:
        def getoption(self, name: str) -> str:
            return str(tmp_path)

    class Item:
        nodeid = "tests/test_example.py::test_empty"
        config = Config()
        stash: dict[Any, Any] = {}

    _save_vm_outputs(Item())  # type: ignore[arg-type]
    assert list(tmp_path.iterdir()) == []
