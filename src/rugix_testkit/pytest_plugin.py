"""Pytest plugin for system tests of Rugix Bakery projects.

Installing ``rugix-testkit`` activates the plugin. By default, the pytest
rootdir is the Bakery project. Configure the plugin in the pytest
configuration::

    [tool.pytest.ini_options]
    rugix_project_dir = "."
    rugix_boot_system = "test-efi"

Tests use the ``vm`` fixture to boot ``rugix_boot_system`` and override the
``boot_system`` fixture to boot another system. The plugin works with
pytest-xdist: bakes run one at a time across workers and each image or bundle
is baked at most once per run.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Generator, Mapping
from contextlib import ExitStack
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread

import pytest

from .bakery import BakeryBuilder, Parameters
from .bundler import Bundler
from .keys import ensure_ssh_key
from .qemu.config import VMConfig
from .qemu.efi import amd64_efi_config
from .qemu.handle import VMHandle
from .qemu.vm import QemuVM
from .report import DESCRIPTION_PROPERTY, describe
from .report import configure as configure_report
from .rugix import RugixCtrl

logger = logging.getLogger(__name__)

EXTENDED_ENV = "RUGIX_TESTS_EXTENDED"
"""Environment variable that enables tests marked as ``extended``."""

QEMU_HOST_ADDRESS = "10.0.2.2"
"""Address of the host as seen from a VM using QEMU's user networking."""

_VMS = pytest.StashKey[list[VMHandle | QemuVM]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "rugix_project_dir",
        "Rugix Bakery project directory, relative to the rootdir",
        default=".",
    )
    parser.addini("rugix_boot_system", "System booted by the `vm` fixture")
    parser.addini(
        "rugix_report_title",
        "Heading of the test report in the GitHub Actions job summary",
        default="System Tests",
    )
    parser.addoption(
        "--test-outputs-dir",
        default="test-outputs",
        help="directory for serial logs and command histories of test VMs",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"extended: test outside the default suite (run with {EXTENDED_ENV}=1)",
    )
    configure_report(config)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        item.user_properties.append((DESCRIPTION_PROPERTY, describe(item)))
    if os.environ.get(EXTENDED_ENV):
        return
    skip = pytest.mark.skip(reason=f"set {EXTENDED_ENV}=1 to enable")
    for item in items:
        if "extended" in item.keywords:
            item.add_marker(skip)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if report.when == "call":
        _save_vm_outputs(item)
    return report


@pytest.fixture(scope="session")
def rugix_project(pytestconfig: pytest.Config) -> Path:
    """The Rugix Bakery project directory."""
    project_dir: str = pytestconfig.getini("rugix_project_dir")
    project = (pytestconfig.rootpath / project_dir).resolve()
    if not (project / "rugix-bakery.toml").is_file():
        pytest.fail(f"no rugix-bakery.toml in {project}")
    return project


@pytest.fixture(scope="session")
def ssh_key(rugix_project: Path) -> Path:
    """Private key authorized for ``root`` on all baked systems."""
    path = rugix_project / ".rugix-testkit" / "id_rsa"
    ensure_ssh_key(path)
    return path


@pytest.fixture(scope="session")
def bakery_parameters() -> Parameters:
    """Recipe parameter overrides for all bakes. Override to add parameters."""
    return {}


@pytest.fixture(scope="session")
def bakery(
    pytestconfig: pytest.Config,
    rugix_project: Path,
    ssh_key: Path,
    bakery_parameters: Parameters,
    tmp_path_factory: pytest.TempPathFactory,
) -> BakeryBuilder:
    """Bakes images and bundles of the project."""
    run_dir = tmp_path_factory.getbasetemp()
    # pytest-xdist gives each worker a base temp directory below one shared by
    # the whole run.
    if hasattr(pytestconfig, "workerinput"):
        run_dir = run_dir.parent
    ssh_parameters: Mapping[str, str | bool | int] = {
        **bakery_parameters.get("core/ssh", {}),
        "root_authorized_keys": ensure_ssh_key(ssh_key),
    }
    parameters = {**bakery_parameters, "core/ssh": ssh_parameters}
    return BakeryBuilder(rugix_project, run_dir, parameters=parameters)


@pytest.fixture(scope="session")
def bundler(rugix_project: Path) -> Bundler:
    """Runs Rugix Bundler on the host."""
    return Bundler(rugix_project)


@pytest.fixture(scope="session")
def http_server(rugix_project: Path) -> Generator[HTTPServer]:
    """Serves the project directory over HTTP to VMs."""
    handler = partial(_QuietHandler, directory=str(rugix_project))
    server = HTTPServer(("0.0.0.0", 0), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    logger.info("HTTP server on port %d", server.server_port)
    yield server
    server.shutdown()


@pytest.fixture
def bundle_url(rugix_project: Path, http_server: HTTPServer) -> Callable[[Path], str]:
    """Returns the URL at which VMs can download a file of the project."""
    base = f"http://{QEMU_HOST_ADDRESS}:{http_server.server_port}"

    def url(path: Path) -> str:
        return f"{base}/{path.resolve().relative_to(rugix_project)}"

    return url


@pytest.fixture
def boot_system(pytestconfig: pytest.Config) -> str:
    """System booted by the ``vm`` fixture."""
    system: str = pytestconfig.getini("rugix_boot_system")
    if not system:
        pytest.fail("set `rugix_boot_system` or override the `boot_system` fixture")
    return system


@pytest.fixture
def boot_timeout() -> float:
    """Seconds to wait for SSH after starting a VM."""
    return 300.0


@pytest.fixture
def start_vm(
    request: pytest.FixtureRequest, ssh_key: Path, boot_timeout: float
) -> Generator[Callable[[VMConfig], VMHandle]]:
    """Starts VMs that are stopped after the test.

    The serial output and command history of each VM are saved to the test
    outputs directory.
    """
    with ExitStack() as stack:

        def start(config: VMConfig) -> VMHandle:
            handle = stack.enter_context(
                VMHandle.start(config, private_key=ssh_key, boot_timeout=boot_timeout)
            )
            request.node.stash.setdefault(_VMS, []).append(handle)
            return handle

        yield start


@pytest.fixture
def start_qemu(
    request: pytest.FixtureRequest,
) -> Generator[Callable[[VMConfig], QemuVM]]:
    """Starts VMs without connecting via SSH, for example, to check the serial
    console of a failing boot. The VMs are stopped after the test.

    The serial output of each VM is saved to the test outputs directory.
    """
    vms: list[QemuVM] = []

    def start(config: VMConfig) -> QemuVM:
        qemu = QemuVM(config)
        qemu.prepare()
        vms.append(qemu)
        qemu.start()
        request.node.stash.setdefault(_VMS, []).append(qemu)
        return qemu

    yield start
    for qemu in vms:
        qemu.stop()
        qemu.cleanup()


@pytest.fixture
def vm(
    bakery: BakeryBuilder,
    boot_system: str,
    start_vm: Callable[[VMConfig], VMHandle],
) -> VMHandle:
    """A VM running ``boot_system``."""
    return start_vm(amd64_efi_config(bakery.bake_image(boot_system)))


@pytest.fixture
def rugix(vm: VMHandle) -> RugixCtrl:
    """Rugix Ctrl on the ``vm``."""
    return RugixCtrl(vm)


def _save_vm_outputs(item: pytest.Item) -> None:
    handles = item.stash.get(_VMS, [])
    if not handles:
        return
    outputs_dir = Path(item.config.getoption("--test-outputs-dir"))
    test_dir = outputs_dir / item.nodeid.replace("::", "--").replace("/", "--")
    test_dir.mkdir(parents=True, exist_ok=True)
    for index, handle in enumerate(handles):
        suffix = f"-{index}" if index else ""
        (test_dir / f"serial{suffix}.log").write_text(handle.serial_output)
        if isinstance(handle, VMHandle):
            (test_dir / f"commands{suffix}.log").write_text(
                "".join(f"{cmd}\n\n" for cmd in handle.command_history)
            )


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        logger.debug("http: " + format, *args)
