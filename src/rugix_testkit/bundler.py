"""Run Rugix Bundler on the host with the binary under test."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class Bundler:
    """Pack apps and bundles, sign bundles, and create deltas on the host."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir.resolve()
        executable = os.environ.get("RUGIX_BUNDLER")
        binaries_dir = os.environ.get("RUGIX_BINARIES_DIR")
        if executable:
            self.executable = executable
        elif binaries_dir:
            targets = {
                "x86_64": "x86_64-unknown-linux-musl",
                "aarch64": "aarch64-unknown-linux-musl",
            }
            target = targets.get(platform.machine())
            if target is None or platform.system() != "Linux":
                raise ValueError("set RUGIX_BUNDLER for this host platform")
            self.executable = str(
                Path(binaries_dir).resolve() / target / "rugix-bundler"
            )
        else:
            self.executable = "rugix-bundler"

    def run(self, args: list[str]) -> None:
        """Run Bundler in the test project directory."""
        cmd = [self.executable, *args]
        logger.info("bundler: %s", " ".join(cmd))
        subprocess.run(cmd, cwd=self.project_dir, check=True)

    def bundle(self, source_dir: Path, output: Path) -> Path:
        """``bundler bundle <dir> <out>``. Returns *output*."""
        output.parent.mkdir(parents=True, exist_ok=True)
        self.run(["bundle", str(source_dir), str(output)])
        return output

    def delta(self, base: Path, target: Path, output: Path) -> Path:
        """``bundler delta <base> <target> <out>``."""
        self.run(["delta", str(base), str(target), str(output)])
        return output

    def sign(self, bundle: Path, root_crt: Path, root_key: Path, output: Path) -> Path:
        """``bundler signatures sign <bundle> <crt> <key> <out>``."""
        self.run(
            [
                "signatures",
                "sign",
                str(bundle),
                str(root_crt),
                str(root_key),
                str(output),
            ]
        )
        return output

    def apps_pack(
        self, kind: str, args: list[str], input_path: Path, output: Path
    ) -> Path:
        """``bundler apps pack <kind> [args...] <input> <out>``.

        *args* slot in between the kind and the trailing positional
        ``<input> <out>`` (e.g. ``--app NAME``, ``--include PATH``).
        """
        self.run(
            [
                "apps",
                "pack",
                kind,
                *args,
                str(input_path),
                str(output),
            ]
        )
        return output
