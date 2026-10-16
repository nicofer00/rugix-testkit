# Rugix Testkit

> [!NOTE]
> **Support:** This is an internal project used for testing [Rugix](https://rugix.org)
> builds. It is not intended for general use, and its API may change without
> notice.

Rugix Testkit provides the shared infrastructure for the system tests of
[Rugix Ctrl](https://github.com/rugix/rugix),
[Rugix Bakery](https://github.com/rugix/rugix-bakery), and
[Rugix Admin](https://github.com/rugix/rugix-admin). A system test bakes an
operating system image with Rugix Bakery, boots it in a QEMU virtual machine
(VM), and checks the system's behavior over SSH, for example, by installing an
update and rebooting into it.

The testkit consists of three parts:

- A Python library for running QEMU VMs and controlling Rugix Ctrl in them.
- A pytest plugin with fixtures for baking images and booting VMs.
- A Rugix Bakery repository with a base layer for test systems.

## Writing System Tests

A project with system tests is a Rugix Bakery project with a pytest
configuration. Installing `rugix-testkit` activates the pytest plugin:

```toml
# pyproject.toml
[project]
name = "system-tests"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = [
    "rugix-testkit @ git+https://github.com/rugix/rugix-testkit.git",
    "pytest-xdist>=3.0",
]

[tool.pytest.ini_options]
# System booted by the `vm` fixture.
rugix_boot_system = "test-efi"
# Keep each test module on one pytest-xdist worker.
addopts = "--dist loadfile"
```

The project's systems build on the `rugix-testkit/debian-efi` layer. It
contains Debian, GRUB for the `generic-grub-efi` target, Rugix Ctrl with its
privileged daemon, an SSH server, DHCP networking, and curl:

```toml
# rugix-bakery.toml
[repositories]
rugix-testkit = { git = "https://github.com/rugix/rugix-testkit.git", dir = "bakery" }

[systems.test-efi]
layer = "test-efi"
architecture = "amd64"
target = "generic-grub-efi"
```

```toml
# layers/test-efi.toml
parent = "rugix-testkit/debian-efi"
```

The base layer installs the Rugix Ctrl binary that the project mounts into
the Bakery container with `RUGIX_BINARIES_DIR`, falling back to the binary
shipped with the Bakery image. Copy the `run-bakery` script from an existing
test project. It runs the Bakery image named by `RUGIX_BAKERY_IMAGE`, which
must be available locally:

```sh
export RUGIX_BAKERY_IMAGE=ghcr.io/rugix/rugix-bakery:branch-main
podman pull "$RUGIX_BAKERY_IMAGE"
```

A test requests the `vm` fixture to boot the system and the `rugix` fixture to
control Rugix Ctrl on it:

```python
from collections.abc import Callable
from pathlib import Path

from rugix_testkit import BakeryBuilder, RugixCtrl


def test_update(
    rugix: RugixCtrl, bakery: BakeryBuilder, bundle_url: Callable[[Path], str]
) -> None:
    """Install an update and reboot into it."""
    rugix.install_and_reboot(bundle_url(bakery.bake_bundle("test-efi")))
    rugix.assert_boot(default="a", active="b")
```

### Fixtures

| Fixture | Description |
| --- | --- |
| `rugix_project` | Path of the Bakery project, configured by `rugix_project_dir`. |
| `bakery` | Bakes images and bundles of the project. |
| `bakery_parameters` | Recipe parameter overrides for all bakes. Override it to add parameters. |
| `bundler` | Runs Rugix Bundler on the host. |
| `bundle_url` | Converts a path in the project to a URL that VMs can download from. |
| `boot_system` | System booted by `vm`. Override it in a module to boot another system. |
| `boot_timeout` | Seconds to wait for SSH after starting a VM. |
| `start_vm` | Starts a VM from a `VMConfig`, for example, one with a TPM. |
| `start_qemu` | Starts a VM without SSH, for example, to check the serial console. |
| `vm` | A VM running `boot_system`. |
| `rugix` | Rugix Ctrl on `vm`. |
| `ssh_key` | Private key that baked systems authorize for `root`. |

Tests marked with `@pytest.mark.extended` only run when `RUGIX_TESTS_EXTENDED`
is set. After each test, the plugin saves the serial output and command
history of its VMs to `test-outputs/`.

### Baking and Parallel Runs

Rugix Bakery's caches are not safe for concurrent builds, so the `bakery`
fixture runs one bake at a time, even across pytest-xdist workers, and bakes
each image and bundle at most once per test run. The VMs of different workers
run in parallel. Each VM uses 2 vCPUs and 2 GiB of memory, which determines how
many workers a machine can sustain, for example, `pytest -n 4`.

The plugin generates an SSH key in `.rugix-testkit/` and authorizes it with a
parameter override of `core/ssh`. Because Bakery's layer cache keys include
parameter overrides, changing the key or other overrides rebuilds the affected
layers. Bakery does not track the content of mounted binaries, so delete the
project's `.rugix` directory after rebuilding binaries that a cached layer
installed.

### Test Report

When `GITHUB_STEP_SUMMARY` is set, as in GitHub Actions, the plugin appends a
test report to the job summary. The report lists each test with its outcome,
its duration, and the first paragraph of its docstring as description, followed
by the details of failures. Set `rugix_report_title` in the pytest
configuration to change the report's heading.

## Prerequisites

System tests require a container runtime (Podman or Docker) for Rugix Bakery,
`qemu-system-x86_64` with OVMF firmware, and `swtpm` for TPM tests. KVM
acceleration is used when `/dev/kvm` is accessible. Set `RUGIX_OVMF_CODE_AMD64`
and `RUGIX_OVMF_VARS_AMD64` if the firmware is not found automatically.

## Development

Requires [mise](https://mise.jdx.dev/), which installs Python and
[uv](https://docs.astral.sh/uv/):

```sh
mise install
mise run check
```

## Licensing

This project is licensed under either [MIT](https://github.com/rugix/rugix/blob/main/LICENSE-MIT) or [Apache 2.0](https://github.com/rugix/rugix/blob/main/LICENSE-APACHE) at your option.

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in this project by you, as defined in the Apache 2.0 license, shall be dual licensed as above, without any additional terms or conditions.

---

Made with ❤️ for OSS by [Silitics](https://www.silitics.com)
