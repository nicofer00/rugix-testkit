"""Command-line interface for rugix-testkit."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from .qemu.config import Drive, Pflash, VMConfig
from .qemu.handle import VMHandle
from .qemu.vm import QemuVM


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="rugix-testkit", description="Run QEMU virtual machines"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="subcmd")

    p_run = sub.add_parser("run", help="Run a VM interactively")
    _add_common_args(p_run)

    p_ssh = sub.add_parser("ssh", help="Boot a VM and connect via SSH")
    _add_common_args(p_ssh)
    p_ssh.add_argument(
        "--timeout", type=float, default=300, help="Boot timeout in seconds"
    )
    p_ssh.add_argument(
        "ssh_command",
        nargs="*",
        metavar="command",
        help="Command to run (omit for interactive shell)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.subcmd == "run":
        sys.exit(_cmd_run(args))
    elif args.subcmd == "ssh":
        sys.exit(_cmd_ssh(args))
    else:
        parser.print_help()
        sys.exit(1)


def _cmd_run(args: argparse.Namespace) -> int:
    config = _build_config(args)
    qemu = QemuVM(config, work_dir=args.work_dir)
    qemu.prepare()
    return qemu.run()


def _cmd_ssh(args: argparse.Namespace) -> int:
    config = _build_config(args)

    try:
        vm = VMHandle.start(config, work_dir=args.work_dir, boot_timeout=args.timeout)
    except Exception:
        return 1

    if args.ssh_command:
        with vm:
            result = vm.run(args.ssh_command, check=False, hide=True)
            print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            return result.return_code
    else:
        # Interactive SSH: hand off to the ssh binary and clean up QEMU after.
        try:
            proc = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-p",
                    str(vm._ssh_port),
                    "root@127.0.0.1",
                ]
            )
            return proc.returncode
        finally:
            vm.close()


def _build_config(args: argparse.Namespace) -> VMConfig:
    return VMConfig(
        arch=args.arch,
        machine=args.machine,
        memory=args.memory,
        smp=args.smp,
        cpu=args.cpu,
        drives=args.drive or [],
        pflash=args.pflash or [],
        ssh_port=args.ssh_port,
        extra_args=args.qemu_args or [],
    )


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--arch", required=True, help="QEMU architecture (e.g. x86_64, aarch64)"
    )
    parser.add_argument(
        "--machine",
        default=None,
        help="QEMU machine type (default: q35 for x86_64, virt for aarch64)",
    )
    parser.add_argument(
        "--memory", type=int, default=1024, help="Memory in MiB (default: 1024)"
    )
    parser.add_argument(
        "--smp", type=int, default=4, help="Number of CPUs (default: 4)"
    )
    parser.add_argument(
        "--cpu", default=None, help="CPU model (default: auto/host with KVM)"
    )
    parser.add_argument(
        "--ssh-port", type=int, default=None, help="SSH port (default: auto)"
    )
    parser.add_argument(
        "--work-dir", type=Path, default=None, help="Persistent work directory"
    )
    parser.add_argument(
        "--drive",
        type=_parse_drive,
        action="append",
        help="Drive spec: file[,format=raw,interface=virtio,overlay=true,size=16G]",
    )
    parser.add_argument(
        "--pflash",
        type=_parse_pflash,
        action="append",
        help="Pflash spec: file[,format=raw,readonly=true]",
    )
    parser.add_argument(
        "--qemu-args", nargs=argparse.REMAINDER, help="Extra QEMU arguments"
    )


def _parse_drive(value: str) -> Drive:
    """
    Parse a drive specification.

    Format: ``file[,key=value...]``

    Supported keys: ``format``, ``interface``, ``overlay``, ``size``.
    """
    parts = value.split(",")
    file = Path(parts[0])
    kwargs: dict[str, Any] = {}
    for part in parts[1:]:
        k, _, v = part.partition("=")
        if k == "overlay":
            kwargs["overlay"] = v.lower() in ("true", "1", "yes", "")
        elif k in ("format", "interface", "size"):
            kwargs[k] = v
        else:
            raise argparse.ArgumentTypeError(f"Unknown drive option: {k}")
    return Drive(file=file, **kwargs)


def _parse_pflash(value: str) -> Pflash:
    """
    Parse a pflash specification.

    Format: ``[file][,key=value...]``

    Supported keys: ``format``, ``readonly``, ``size``.

    An empty file creates a blank image (requires ``size``).
    """
    parts = value.split(",")
    file = Path(parts[0]) if parts[0] else None
    kwargs: dict[str, Any] = {}
    for part in parts[1:]:
        k, _, v = part.partition("=")
        if k == "readonly":
            kwargs["readonly"] = v.lower() in ("true", "1", "yes", "")
        elif k in ("format", "size"):
            kwargs[k] = v
        else:
            raise argparse.ArgumentTypeError(f"Unknown pflash option: {k}")
    return Pflash(file=file, **kwargs)


if __name__ == "__main__":
    main()
