"""Tests for the test report in the GitHub Actions job summary."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

TESTS = '''
import pytest

def test_passes():
    """Checks that something works.

    Further paragraphs are not part of the description.
    """

def test_fails():
    """Checks | something else."""
    assert 1 == 2

@pytest.mark.skip(reason="not today")
def test_skipped():
    pass

@pytest.mark.xfail(reason="known")
def test_known_failure():
    assert False
'''


def test_report_lists_tests_and_failures(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The report shows each test's outcome and description and the details of
    failures."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    pytester.makepyfile(test_example=TESTS)

    pytester.runpytest().assert_outcomes(passed=1, failed=1, skipped=1, xfailed=1)

    report = summary.read_text()
    assert report.startswith(
        "## System Tests\n\n1 passed, 1 failed, 1 skipped, 1 xfailed in "
    )
    assert (
        "| ✅ | `test_example.py::test_passes` | Checks that something works. |"
        in report
    )
    assert (
        "| ❌ | `test_example.py::test_fails` | Checks \\| something else. |" in report
    )
    assert "| ⏭️ | `test_example.py::test_skipped` |  |" in report
    assert "| ➖ | `test_example.py::test_known_failure` |" in report
    assert (
        "<details><summary><code>test_example.py::test_fails</code></summary>" in report
    )
    assert "assert 1 == 2" in report


def test_no_report_without_summary_file(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``GITHUB_STEP_SUMMARY``, the plugin writes no report."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    pytester.makepyfile(test_example=TESTS)

    pytester.runpytest()

    assert not any(pytester.path.glob("*.md"))
