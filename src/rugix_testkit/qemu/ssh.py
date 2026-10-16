"""SSH connection helpers using Fabric."""

from __future__ import annotations

import json
import logging
import shlex
import socket
import time
from pathlib import Path

import fabric
import paramiko

from ..result import CmdError, CmdResult
from ..types import JsonObject

logger = logging.getLogger(__name__)


def connect_ssh(
    host: str = "127.0.0.1",
    port: int = 2222,
    user: str = "root",
    connect_timeout: float = 10,
    private_key: Path | paramiko.PKey | None = None,
) -> fabric.Connection:
    """
    Open a Fabric SSH connection to a test VM.

    Without *private_key*, authenticates with SSH ``none`` (passwordless
    ``root``). With *private_key* (a path or a paramiko ``PKey``), uses
    public-key authentication. Not suitable for production connections.
    """
    if private_key is not None:
        return _connect_publickey(host, port, user, private_key, connect_timeout)
    return _connect_auth_none(host, port, user)


def wait_for_ssh(
    host: str = "127.0.0.1",
    port: int = 2222,
    user: str = "root",
    timeout: float = 300,
    interval: float = 1,
    private_key: Path | paramiko.PKey | None = None,
) -> fabric.Connection:
    """
    Poll until SSH becomes available and return the open connection.

    See :func:`connect_ssh` for *private_key* semantics.
    """
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        # Paramiko logs tracebacks for connections that never receive a
        # banner, so we only connect once the server sends one.
        if _has_ssh_banner(host, port):
            try:
                return connect_ssh(host, port, user, private_key=private_key)
            except Exception as exc:
                last_error = exc
        time.sleep(interval)
    raise TimeoutError(
        f"SSH not available on {host}:{port} after {timeout}s: {last_error}"
    )


def run_cmd(
    conn: fabric.Connection,
    args: list[str],
    *,
    check: bool = True,
    **kwargs: object,
) -> CmdResult:
    """
    Run a command given as a list of arguments over SSH.

    Arguments are escaped via :func:`shlex.join` so callers never need
    to worry about shell quoting. Raises :class:`CmdError` on non-zero
    exit unless *check* is ``False``.
    """
    command = shlex.join(args)
    # Tell fabric not to raise on non-zero so we can handle it ourselves.
    result = conn.run(command, warn=True, in_stream=False, **kwargs)
    cmd_result = CmdResult(
        command=command,
        stdout=result.stdout,
        stderr=result.stderr,
        return_code=result.return_code,
    )
    if check and not cmd_result.ok:
        raise CmdError(cmd_result)
    return cmd_result


def run_json(
    conn: fabric.Connection,
    args: list[str],
    *,
    check: bool = True,
    **kwargs: object,
) -> JsonObject:
    """
    Run a command and parse its stdout as JSON.

    *args* is a list of command arguments (escaped automatically).
    """
    result = run_cmd(conn, args, check=check, hide=True, **kwargs)
    data: JsonObject = json.loads(result.stdout)
    return data


def _has_ssh_banner(host: str, port: int) -> bool:
    prefix = b""
    try:
        with socket.create_connection((host, port), timeout=2) as sock:
            while len(prefix) < 4 and (chunk := sock.recv(4 - len(prefix))):
                prefix += chunk
    except OSError:
        return False
    return prefix == b"SSH-"


def _connect_auth_none(host: str, port: int, user: str) -> fabric.Connection:
    # Fabric doesn't support SSH "none" auth, so we set up the
    # paramiko transport manually to avoid password auth attempts.
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = paramiko.Transport((host, port))
    sock.connect(username=user)
    sock.auth_none(user)
    client._transport = sock

    conn = fabric.Connection(host=host, port=port, user=user)
    conn.client = client
    conn.transport = sock
    logger.info("SSH connected to %s:%d (auth_none)", host, port)
    return conn


def _connect_publickey(
    host: str,
    port: int,
    user: str,
    key: Path | paramiko.PKey,
    connect_timeout: float,
) -> fabric.Connection:
    pkey = _load_key(key) if isinstance(key, Path) else key
    conn = fabric.Connection(
        host=host,
        port=port,
        user=user,
        connect_timeout=connect_timeout,
        connect_kwargs={
            "pkey": pkey,
            "look_for_keys": False,
            "allow_agent": False,
        },
    )
    conn.open()
    logger.info("SSH connected to %s:%d (publickey)", host, port)
    return conn


def _load_key(path: Path) -> paramiko.PKey:
    """Try the common private-key formats and return the first one that loads."""
    errors: list[Exception] = []
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            return cls.from_private_key_file(str(path))
        except paramiko.SSHException as exc:
            errors.append(exc)
    raise paramiko.SSHException(
        f"Could not load SSH private key {path!r}: {[str(e) for e in errors]}"
    )
