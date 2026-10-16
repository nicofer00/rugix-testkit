"""SSH keys for logging into test VMs."""

from __future__ import annotations

from pathlib import Path

import paramiko

from .locks import exclusive_lock


def ensure_ssh_key(path: Path) -> str:
    """Create an RSA key pair at *path* and ``<path>.pub`` unless it exists.

    Returns the public key in ``authorized_keys`` format. Keeping the key
    across runs lets Bakery reuse cached layers that contain it.
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
    return public_path.read_text().strip()
