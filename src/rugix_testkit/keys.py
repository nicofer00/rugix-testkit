"""SSH keys for logging into test VMs."""

from __future__ import annotations

import shutil
from pathlib import Path

import paramiko

from .locks import exclusive_lock


def ensure_ssh_key(path: Path) -> str:
    """Create an RSA key pair at *path* and ``<path>.pub`` unless it exists.

    Returns the public key in ``authorized_keys`` format. Keeping the key
    across runs lets Bakery reuse cached layers that contain it when SSH is
    authorized via ``--param-file``.
    """
    public_path = path.with_name(f"{path.name}.pub")
    with exclusive_lock(path.with_name(f"{path.name}.lock")):
        if not public_path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            key = paramiko.RSAKey.generate(3072)
            key.write_private_key_file(str(path))
            public_path.write_text(
                f"{key.get_name()} {key.get_base64()} rugix-testkit\n"
            )
            path.chmod(0o600)
            public_path.chmod(0o644)
    return public_path.read_text().strip()


def install_ssh_key(source: Path, dest: Path) -> str:
    """Install a project private key at *dest* (and ``<dest>.pub``).

    Uses ``<source>.pub`` when present; otherwise derives the public key with
    paramiko. Returns the public key in ``authorized_keys`` format.
    """
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"SSH private key not found: {source}")
    public_source = source.with_name(f"{source.name}.pub")
    dest_public = dest.with_name(f"{dest.name}.pub")
    with exclusive_lock(dest.with_name(f"{dest.name}.lock")):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        dest.chmod(0o600)
        if public_source.is_file():
            shutil.copyfile(public_source, dest_public)
        else:
            key = paramiko.RSAKey.from_private_key_file(str(dest))
            dest_public.write_text(
                f"{key.get_name()} {key.get_base64()} rugix-testkit\n"
            )
        dest_public.chmod(0o644)
    return dest_public.read_text().strip()
