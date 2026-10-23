"""Tests for line-streaming subprocess helper."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from rugix_testkit.process import run_logged


def test_run_logged_streams_debug_lines(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    log = logging.getLogger("test_run_logged")
    with caplog.at_level(logging.DEBUG, logger="test_run_logged"):
        run_logged(
            ["sh", "-c", "printf 'one\\ntwo\\n'"],
            logger=log,
            prefix="bakery: ",
            log_file=tmp_path / "out.log",
        )
    assert "bakery: one" in caplog.text
    assert "bakery: two" in caplog.text
    assert (tmp_path / "out.log").read_text() == "one\ntwo\n"


def test_run_logged_raises_with_output() -> None:
    log = logging.getLogger("test_run_logged_fail")
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        run_logged(
            ["sh", "-c", "printf 'fail-line\\n'; exit 2"],
            logger=log,
            prefix="bakery: ",
        )
    assert exc_info.value.returncode == 2
    assert exc_info.value.output == "fail-line\n"


def test_run_logged_without_log_file(tmp_path: Path) -> None:
    log = logging.getLogger("test_run_logged_nofile")
    run_logged(["sh", "-c", "echo ok"], cwd=tmp_path, logger=log, prefix="x: ")
    assert not any(tmp_path.iterdir())
