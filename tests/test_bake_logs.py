"""Integration test: real bake via the ``bakery`` fixture, assert on bake.log.

Requires a local Bakery image and container runtime. Run with::

    export RUGIX_TESTS_EXTENDED=1
    export RUGIX_BAKERY_IMAGE=ghcr.io/rugix/rugix-bakery:v0.9
    uv run pytest tests/test_bake_logs.py -vv --test-outputs-dir=test-outputs
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from rugix_testkit.bakery import BakeryBuilder, Parameters, parameters_hash
from rugix_testkit.pytest_plugin import EXTENDED_ENV

pytestmark = pytest.mark.extended

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bake-project"
_REPO_BAKERY = Path(__file__).resolve().parents[1] / "bakery"


@pytest.fixture(scope="session")
def rugix_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Minimal Bakery project wired to this repo's ``bakery/`` repository."""
    if not (_REPO_BAKERY / "rugix-repository.toml").is_file():
        pytest.fail(f"missing testkit bakery repository at {_REPO_BAKERY}")
    project = tmp_path_factory.mktemp("bake-project")
    shutil.copy(_FIXTURE / "rugix-bakery.toml", project / "rugix-bakery.toml")
    run_bakery = project / "run-bakery"
    shutil.copy(_FIXTURE / "run-bakery", run_bakery)
    run_bakery.chmod(run_bakery.stat().st_mode | 0o111)
    # Copy (do not symlink): run-bakery bind-mounts the project into the
    # container, so host absolute symlinks are invisible there.
    shutil.copytree(_REPO_BAKERY, project / "repos" / "rugix-testkit")
    # Prefer a concrete image; BakeryBuilder sets RUGIX_DEV=true which would
    # otherwise default run-bakery to the :dev tag.
    os.environ.setdefault("RUGIX_BAKERY_IMAGE", "ghcr.io/rugix/rugix-bakery:v0.9")
    return project


@pytest.fixture
def boot_system() -> str:
    return "test-efi"


@pytest.fixture(scope="session")
def bakery(
    pytestconfig: pytest.Config,
    rugix_project: Path,
    ssh_key: Path,
    bakery_parameters: Parameters,
    tmp_path_factory: pytest.TempPathFactory,
) -> BakeryBuilder:
    """Session bakery with bake logs forced under ``--test-outputs-dir/bakery``."""
    from rugix_testkit.pytest_plugin import (
        _bakery_run_dir,
        _session_bakery_parameters,
    )

    ssh_param_file: bool = pytestconfig.getini("rugix_ssh_param_file")
    parameters = _session_bakery_parameters(
        ssh_param_file=ssh_param_file,
        ssh_key=ssh_key,
        bakery_parameters=bakery_parameters,
    )
    outputs = Path(pytestconfig.getoption("--test-outputs-dir")) / "bakery"
    return BakeryBuilder(
        rugix_project,
        _bakery_run_dir(pytestconfig, tmp_path_factory),
        parameters=parameters,
        bake_log_dir=outputs,
    )


def test_bake_image_log_preserves_steps(
    bakery: BakeryBuilder, boot_system: str, pytestconfig: pytest.Config
) -> None:
    """A real ``bake image`` writes ``test-outputs/bakery/<artifact>.log`` with steps.

    TTY status redraws must not delete completed steps, and must not flood the
    log with thousands of duplicate viewport frames. Inspect the log under
    ``--test-outputs-dir``.
    """
    if not os.environ.get(EXTENDED_ENV):
        pytest.skip(f"set {EXTENDED_ENV}=1 to run real bakery bakes")
    if shutil.which("podman") is None and shutil.which("docker") is None:
        pytest.skip("podman or docker required to bake")

    image = bakery.bake_image(boot_system)
    assert image.is_file()
    assert image.stat().st_size > 0

    overrides = parameters_hash(bakery.parameters or None)
    log_path = (
        Path(pytestconfig.getoption("--test-outputs-dir"))
        / "bakery"
        / f"image-{boot_system}-{overrides}.log"
    )
    assert log_path.is_file(), f"expected bake log at {log_path}"
    text = log_path.read_text()
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines, "bake log is empty"
    assert "\x1b" not in text, "ANSI escapes should be stripped from bake.log"

    # Layer headers from a real bake.
    layer_headers = [line for line in lines if "Layer:" in line]
    assert layer_headers, "expected Layer: headers in bake.log"
    # Redraw spam regression: one header line should not appear hundreds of times.
    assert len(layer_headers) < 50, (
        f"too many Layer: lines ({len(layer_headers)}); cursor redraws not deduped?"
    )
    assert len(lines) < 5000, f"bake.log too large ({len(lines)} lines)"
    assert boot_system in text or "bake" in text.lower() or "debian" in text.lower()
