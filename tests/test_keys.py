"""Tests for SSH key helpers."""

from __future__ import annotations

from pathlib import Path

import paramiko

from rugix_testkit.keys import ensure_ssh_key, install_ssh_key


def test_ensure_ssh_key_generates_and_reuses(tmp_path: Path) -> None:
    path = tmp_path / "id_rsa"
    public = ensure_ssh_key(path)
    assert path.is_file()
    assert path.with_name("id_rsa.pub").is_file()
    assert public.startswith("ssh-rsa ")
    assert ensure_ssh_key(path) == public
    private_bytes = path.read_bytes()
    ensure_ssh_key(path)
    assert path.read_bytes() == private_bytes


def test_install_ssh_key_copies_keypair(tmp_path: Path) -> None:
    source = tmp_path / "source_key"
    key = paramiko.RSAKey.generate(2048)
    key.write_private_key_file(str(source))
    source.with_name("source_key.pub").write_text(
        f"{key.get_name()} {key.get_base64()} test\n"
    )
    dest = tmp_path / "dest" / "id_rsa"
    public = install_ssh_key(source, dest)
    assert dest.is_file()
    assert dest.with_name("id_rsa.pub").read_text().strip() == public
    assert "test" in public


def test_install_ssh_key_derives_pub_when_missing(tmp_path: Path) -> None:
    source = tmp_path / "only_private"
    key = paramiko.RSAKey.generate(2048)
    key.write_private_key_file(str(source))
    dest = tmp_path / "id_rsa"
    public = install_ssh_key(source, dest)
    assert public.startswith("ssh-rsa ")
    assert dest.with_name("id_rsa.pub").is_file()
