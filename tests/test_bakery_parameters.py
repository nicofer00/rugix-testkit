"""Tests for parameter merge/hash and bakery builder wiring."""

from __future__ import annotations

from pathlib import Path

from rugix_testkit.bakery import (
    BakeryBuilder,
    merge_parameters,
    parameters_hash,
)
from rugix_testkit.pytest_plugin import _session_bakery_parameters


def test_merge_parameters_later_wins() -> None:
    merged = merge_parameters(
        {"core/ssh": {"root_authorized_keys": "a", "keep": "1"}},
        {"core/ssh": {"root_authorized_keys": "b"}, "other": {"x": 1}},
    )
    assert merged == {
        "core/ssh": {"root_authorized_keys": "b", "keep": "1"},
        "other": {"x": 1},
    }


def test_parameters_hash_stable_and_distinct() -> None:
    a = {"core/ssh": {"root_authorized_keys": "one"}}
    b = {"core/ssh": {"root_authorized_keys": "two"}}
    assert parameters_hash(a) == parameters_hash(a)
    assert parameters_hash(a) != parameters_hash(b)
    assert parameters_hash(None) == "none"
    assert parameters_hash({}) == "none"


def test_session_bakery_parameters_ssh_flag(tmp_path: Path) -> None:
    private = tmp_path / "id_rsa"
    private.write_text("unused-private")
    pub = tmp_path / "id_rsa.pub"
    pub.write_text("ssh-rsa AAAA testkit\n")

    with_ssh = _session_bakery_parameters(
        ssh_param_file=True,
        ssh_key=private,
        bakery_parameters={},
    )
    assert with_ssh is not None
    assert with_ssh["core/ssh"]["root_authorized_keys"] == "ssh-rsa AAAA testkit"

    without_ssh = _session_bakery_parameters(
        ssh_param_file=False,
        ssh_key=private,
        bakery_parameters={},
    )
    assert without_ssh is None

    custom = _session_bakery_parameters(
        ssh_param_file=False,
        ssh_key=private,
        bakery_parameters={"other/recipe": {"flag": True}},
    )
    assert custom == {"other/recipe": {"flag": True}}


def test_bakery_builder_uses_hashed_param_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    parameters = {"core/ssh": {"root_authorized_keys": "ssh-rsa AAAA"}}
    builder = BakeryBuilder(project, run_dir, parameters=parameters)
    assert builder._bake_args[0] == "--param-file"
    param_path = project / builder._bake_args[1]
    assert param_path.is_file()
    assert parameters_hash(parameters) in param_path.name
    assert "image-sys-" + parameters_hash(parameters) == (
        f"image-sys-{builder._overrides_hash}"
    )
