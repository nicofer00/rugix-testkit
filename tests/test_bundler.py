"""Tests for selecting the Rugix Bundler binary."""

from pathlib import Path

import pytest

from rugix_testkit.bundler import Bundler


def test_bundler_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``RUGIX_BUNDLER`` takes precedence over ``RUGIX_BINARIES_DIR``."""
    monkeypatch.setenv("RUGIX_BUNDLER", "/opt/rugix-bundler")
    monkeypatch.setenv("RUGIX_BINARIES_DIR", str(tmp_path))

    assert Bundler(tmp_path).executable == "/opt/rugix-bundler"


@pytest.mark.parametrize(
    ("machine", "target"),
    [
        ("x86_64", "x86_64-unknown-linux-musl"),
        ("aarch64", "aarch64-unknown-linux-musl"),
    ],
)
def test_bundler_from_binaries_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, machine: str, target: str
) -> None:
    """``RUGIX_BINARIES_DIR`` selects the musl build for the host architecture."""
    monkeypatch.delenv("RUGIX_BUNDLER", raising=False)
    monkeypatch.setenv("RUGIX_BINARIES_DIR", str(tmp_path))
    monkeypatch.setattr("platform.machine", lambda: machine)
    monkeypatch.setattr("platform.system", lambda: "Linux")

    executable = Bundler(tmp_path).executable

    assert executable == str(tmp_path.resolve() / target / "rugix-bundler")
