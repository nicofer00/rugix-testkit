"""Tests for line-streaming subprocess helper."""

from __future__ import annotations

import logging
import subprocess
import time
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


def test_run_logged_strips_crlf(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("test_run_logged_crlf")
    with caplog.at_level(logging.DEBUG, logger="test_run_logged_crlf"):
        run_logged(
            ["sh", "-c", "printf 'one\\r\\ntwo\\r\\n'"],
            logger=log,
            prefix="bakery: ",
        )
    # No blank DEBUG lines between the two bakery lines.
    bakery_lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "test_run_logged_crlf"
    ]
    assert bakery_lines == ["bakery: one", "bakery: two"]


def test_run_logged_streams_live_with_pty() -> None:
    """PTY forces line buffering so sleeps between prints are visible live."""
    log = logging.getLogger("test_run_logged_live")
    seen: list[float] = []

    class Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.getMessage().startswith("bakery: "):
                seen.append(time.monotonic())

    handler = Handler()
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        run_logged(
            [
                "python",
                "-c",
                "import time\n"
                "print('a', flush=False)\n"
                "time.sleep(0.4)\n"
                "print('b', flush=False)\n",
            ],
            logger=log,
            prefix="bakery: ",
        )
    finally:
        log.removeHandler(handler)

    assert len(seen) == 2
    assert seen[1] - seen[0] >= 0.25
