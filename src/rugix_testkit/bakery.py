"""Bake images and bundles with a project's ``run-bakery`` script.

The bake stage runs inside the Rugix Bakery container. The project directory
is bind-mounted into the container, so artifacts appear on the host under
``build/``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

from .locks import exclusive_lock
from .process import run_logged

logger = logging.getLogger(__name__)

Parameters = Mapping[str, Mapping[str, str | bool | int]]
"""Recipe parameter overrides, keyed by recipe and then by parameter name.

Recipes are referenced as in the project's layers, e.g., ``core/ssh``.
"""


def merge_parameters(
    *parts: Parameters | None,
) -> dict[str, dict[str, str | bool | int]]:
    """Merge parameter maps; later parts override earlier ones per recipe key."""
    merged: dict[str, dict[str, str | bool | int]] = {}
    for part in parts:
        if not part:
            continue
        for recipe, values in part.items():
            merged.setdefault(recipe, {}).update(values)
    return merged


def parameters_hash(parameters: Parameters | None) -> str:
    """Stable short hash of *parameters* for bake markers and param-file names."""
    if not parameters:
        return "none"
    payload = _parameters_toml(parameters).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


class BakeryBuilder:
    """Bakes each image and bundle at most once per test run.

    Bakery's caches are not safe for concurrent builds, so bakes are
    serialized across pytest-xdist workers with a lock file in *run_dir*. All
    workers of a test run must share *run_dir*, which also records the
    artifacts baked during the run.

    *parameters* override recipe parameters for every bake via ``--param-file``.
    Prefer layer.toml parameters for fixed values (for example, an SSH public
    key) so parent layer caches stay reusable. When using multiple builders
    with different overrides for the same system name, later bakes overwrite
    ``build/<system>/``; marker keys include an overrides hash so a second
    bake is not skipped.
    """

    def __init__(
        self,
        project_dir: Path,
        run_dir: Path,
        *,
        parameters: Parameters | None = None,
        bake_log_dir: Path | None = None,
    ) -> None:
        self.project_dir = project_dir.resolve()
        self.run_dir = run_dir
        self.bake_log_dir = bake_log_dir
        self.parameters: dict[str, dict[str, str | bool | int]] = (
            dict(merge_parameters(parameters)) if parameters else {}
        )
        self._overrides_hash = parameters_hash(self.parameters or None)
        self._bake_args: list[str] = []
        self._bake_log_file: Path | None = None
        if self.parameters:
            param_file = (
                self.project_dir
                / ".rugix-testkit"
                / f"parameters-{self._overrides_hash}.toml"
            )
            _write_atomically(param_file, _parameters_toml(self.parameters))
            relative = param_file.relative_to(self.project_dir)
            self._bake_args = ["--param-file", str(relative)]

    def bake_image(self, system: str) -> Path:
        """Bake a boot image. Returns the path to a copy for booting VMs.

        ``bake bundle`` rewrites ``system.img`` in place, which would corrupt
        the backing image of VMs booted from it.
        """
        output = self.project_dir / "build" / system
        image = output / "vm.img"

        def bake() -> None:
            self._run(["bake", "image", *self._bake_args, system])
            shutil.copyfile(output / "system.img", image)

        self._bake_once(f"image-{system}-{self._overrides_hash}", bake)
        return image

    def bake_bundle(self, system: str) -> Path:
        """Bake an update bundle. Returns the path to ``system.rugixb``."""
        self._bake_once(
            f"bundle-{system}-{self._overrides_hash}",
            lambda: self._run(["bake", "bundle", *self._bake_args, system]),
        )
        return self.project_dir / "build" / system / "system.rugixb"

    def test(self, workflow: str) -> None:
        """Run a ``rugix-bakery test`` workflow with the parameter overrides."""
        self.run(["test", *self._bake_args, workflow])

    def run(self, args: list[str]) -> None:
        """Run ``run-bakery`` with *args*, for example, a ``bundler`` command.

        The command runs exclusively, like bakes.
        """
        with exclusive_lock(self.run_dir / "bakery.lock"):
            self._run(args)

    def _run(self, args: list[str]) -> None:
        cmd = ["./run-bakery", *args]
        logger.info("bakery: %s", " ".join(cmd))
        env = os.environ.copy()
        env.setdefault("RUGIX_DEV", "true")
        run_logged(
            cmd,
            cwd=self.project_dir,
            env=env,
            logger=logger,
            prefix="bakery: ",
            log_file=self._bake_log_file,
        )

    def _bake_once(self, artifact: str, bake: Callable[[], None]) -> None:
        marker = self.run_dir / f"baked-{artifact}"
        if marker.exists():
            return
        with exclusive_lock(self.run_dir / "bakery.lock"):
            # Another worker may have baked the artifact while we waited.
            if marker.exists():
                return
            log_file: Path | None = None
            if self.bake_log_dir is not None:
                self.bake_log_dir.mkdir(parents=True, exist_ok=True)
                log_file = self.bake_log_dir / f"{artifact}.log"
            self._bake_log_file = log_file
            try:
                bake()
            finally:
                self._bake_log_file = None
            marker.touch()


def _parameters_toml(parameters: Parameters) -> str:
    # JSON strings, numbers, and booleans are valid TOML values.
    lines = []
    for recipe, values in sorted(parameters.items()):
        lines.append(f"[{json.dumps(recipe)}]")
        lines.extend(
            f"{json.dumps(name)} = {json.dumps(value)}"
            for name, value in sorted(values.items())
        )
    return "\n".join(lines) + "\n"


def _write_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(content)
    tmp.replace(path)
