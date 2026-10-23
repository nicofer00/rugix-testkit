"""Unit tests for line-streaming subprocess helper."""

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


def test_run_logged_cr_overwrites_current_line_only(tmp_path: Path) -> None:
    """Bare CR replaces the current line; prior lines stay in the transcript."""
    log = logging.getLogger("test_run_logged_progress")
    code = (
        "import sys\n"
        "sys.stdout.write('keep\\n')\n"
        "sys.stdout.write('\\rprogress 10%')\n"
        "sys.stdout.write('\\rprogress 100%\\n')\n"
        "sys.stdout.write('\\x1b[1A\\x1b[2Kignored-cursor\\n')\n"
    )
    log_path = tmp_path / "out.log"
    run_logged(
        ["python", "-c", code],
        logger=log,
        prefix="bakery: ",
        log_file=log_path,
    )
    assert log_path.read_text().splitlines() == [
        "keep",
        "progress 100%",
        "ignored-cursor",
    ]


def test_run_logged_cursor_up_does_not_delete_steps(tmp_path: Path) -> None:
    """Layer-style cursor redraws must not drop earlier steps from bake.log."""
    log = logging.getLogger("test_run_logged_layer")
    code = (
        "import sys\n"
        "sys.stdout.write('------- Layer: current-layer -------\\n')\n"
        "sys.stdout.write('running step 1\\n')\n"
        "sys.stdout.write('\\x1b[2A\\x1b[2K------- Layer: current-layer -------\\n')\n"
        "sys.stdout.write('\\x1b[2Krunning step 2\\n')\n"
        "sys.stdout.write('\\x1b[2A\\x1b[2K------- Layer: current-layer -------\\n')\n"
        "sys.stdout.write('\\x1b[2Kdone\\n')\n"
    )
    log_path = tmp_path / "layer.log"
    run_logged(
        ["python", "-c", code],
        logger=log,
        prefix="bakery: ",
        log_file=log_path,
    )
    lines = log_path.read_text().splitlines()
    assert "running step 1" in lines
    assert "running step 2" in lines
    assert "done" in lines
    assert lines.count("------- Layer: current-layer -------") == 1
