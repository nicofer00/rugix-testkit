"""Tests for line-streaming subprocess helper."""

from __future__ import annotations

import logging
import subprocess
import sys
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
    bakery_lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "test_run_logged_crlf"
    ]
    assert bakery_lines == ["bakery: one", "bakery: two"]


def test_run_logged_replaces_in_place_updates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CR progress and cursor-up redraws replace lines instead of duplicating."""
    log = logging.getLogger("test_run_logged_progress")
    code = (
        "import sys\n"
        "sys.stdout.write('keep\\n')\n"
        "sys.stdout.write('\\rprogress 10%')\n"
        "sys.stdout.write('\\rprogress 100%\\n')\n"
        "sys.stdout.write('\\x1b[1A\\x1b[2Kfinal\\n')\n"
    )
    with caplog.at_level(logging.DEBUG, logger="test_run_logged_progress"):
        run_logged(["python", "-c", code], logger=log, prefix="bakery: ")
    bakery_lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "test_run_logged_progress"
    ]
    assert bakery_lines == ["bakery: keep", "bakery: final"]
    assert all("\x1b" not in msg for msg in bakery_lines)


def test_run_logged_layer_style_redraw(caplog: pytest.LogCaptureFixture) -> None:
    """A multi-line status block rewritten with cursor-up logs once."""
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
    with caplog.at_level(logging.DEBUG, logger="test_run_logged_layer"):
        run_logged(["python", "-c", code], logger=log, prefix="bakery: ")
    bakery_lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "test_run_logged_layer"
    ]
    assert bakery_lines == [
        "bakery: ------- Layer: current-layer -------",
        "bakery: done",
    ]


def test_run_logged_flushes_soft_lines_on_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Soft status is still logged when the child exits non-zero."""
    log = logging.getLogger("test_run_logged_fail_soft")
    code = (
        "import sys\n"
        "sys.stdout.write('------- Layer: current-layer -------\\n')\n"
        "sys.stdout.write('running\\n')\n"
        "sys.stdout.write('\\x1b[1A\\x1b[2Kfailed step\\n')\n"
        "sys.exit(3)\n"
    )
    with caplog.at_level(logging.DEBUG, logger="test_run_logged_fail_soft"):
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            run_logged(["python", "-c", code], logger=log, prefix="bakery: ")
    assert exc_info.value.returncode == 3
    assert "failed step" in exc_info.value.output
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "test_run_logged_fail_soft"
    ]
    assert "bakery: ------- Layer: current-layer -------" in messages
    assert "bakery: failed step" in messages


def test_run_logged_soft_overwrite_on_tty(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """On a TTY, soft updates use CSI clears; only the final line is logged."""

    class TtyStderr:
        def __init__(self) -> None:
            self.buf = ""

        def isatty(self) -> bool:
            return True

        def write(self, data: str) -> int:
            self.buf += data
            return len(data)

        def flush(self) -> None:
            return None

    tty = TtyStderr()
    monkeypatch.setattr(sys, "stderr", tty)
    log = logging.getLogger("test_run_logged_tty_soft")
    code = (
        "import sys\n"
        "sys.stdout.write('progress 10%')\n"
        "sys.stdout.flush()\n"
        "sys.stdout.write('\\rprogress 100%\\n')\n"
        "sys.stdout.flush()\n"
    )
    with caplog.at_level(logging.DEBUG, logger="test_run_logged_tty_soft"):
        run_logged(["python", "-c", code], logger=log, prefix="bakery: ")
    assert "\x1b[2K" in tty.buf
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "test_run_logged_tty_soft"
    ]
    assert messages == ["bakery: progress 100%"]


def test_run_logged_streams_live_with_pty() -> None:
    """PTY + soft-tail of one still streams earlier lines before later ones."""
    log = logging.getLogger("test_run_logged_live")
    seen: list[tuple[float, str]] = []

    class Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            msg = record.getMessage()
            if msg.startswith("bakery: "):
                seen.append((time.monotonic(), msg))

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
                "print('b', flush=False)\n"
                "time.sleep(0.4)\n"
                "print('c', flush=False)\n",
            ],
            logger=log,
            prefix="bakery: ",
        )
    finally:
        log.removeHandler(handler)

    assert [msg for _, msg in seen] == ["bakery: a", "bakery: b", "bakery: c"]
    # soft-tail=1 emits "a" when "b" arrives (after the first sleep).
    assert seen[1][0] - seen[0][0] >= 0.25
