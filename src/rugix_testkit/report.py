"""Test report for the job summary of GitHub Actions.

When ``GITHUB_STEP_SUMMARY`` is set, the plugin appends a Markdown report to
that file. The report lists every test with its outcome, duration, and the
first paragraph of its docstring, followed by the details of failures.
"""

from __future__ import annotations

import inspect
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SUMMARY_ENV = "GITHUB_STEP_SUMMARY"
"""Environment variable with the path of the job summary file."""

DESCRIPTION_PROPERTY = "description"
"""User property that carries a test's description to the report."""

MAX_FAILURE_LINES = 60
"""Number of trailing lines of a failure that the report shows."""

_ICONS = {
    "passed": "✅",
    "failed": "❌",
    "error": "💥",
    "skipped": "⏭️",
    "xfailed": "➖",
    "xpassed": "⚠️",
}


def describe(item: pytest.Item) -> str:
    """Return the first paragraph of the docstring of *item* on one line."""
    obj = getattr(item, "obj", None)
    doc = inspect.getdoc(obj) if obj is not None else None
    if not doc:
        return ""
    return " ".join(doc.split("\n\n", 1)[0].split())


@dataclass
class _Result:
    nodeid: str
    description: str = ""
    outcome: str = "passed"
    duration: float = 0.0
    details: list[str] = field(default_factory=list)


class SummaryReporter:
    """Collects test reports and writes the summary at the end of the session."""

    def __init__(self, path: Path, title: str) -> None:
        self.path = path
        self.title = title
        self.results: dict[str, _Result] = {}
        self.start = time.monotonic()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        result = self.results.setdefault(report.nodeid, _Result(report.nodeid))
        result.duration += report.duration
        for name, value in report.user_properties:
            if name == DESCRIPTION_PROPERTY:
                result.description = str(value)
        if hasattr(report, "wasxfail"):
            result.outcome = "xfailed" if report.skipped else "xpassed"
        elif report.failed:
            result.outcome = "failed" if report.when == "call" else "error"
            result.details.append(report.longreprtext)
        elif report.skipped and result.outcome == "passed":
            result.outcome = "skipped"

    def pytest_sessionfinish(self) -> None:
        with self.path.open("a", encoding="utf-8") as summary:
            summary.write(self.render(time.monotonic() - self.start))

    def render(self, elapsed: float) -> str:
        """Render the report as Markdown."""
        results = sorted(self.results.values(), key=lambda result: result.nodeid)
        counts: dict[str, int] = {}
        for result in results:
            counts[result.outcome] = counts.get(result.outcome, 0) + 1
        totals = ", ".join(
            f"{count} {outcome}" for outcome in _ICONS if (count := counts.get(outcome))
        )
        lines = [
            f"## {self.title}",
            "",
            f"{totals or 'no tests'} in {_format_duration(elapsed)}",
            "",
            "| | Test | Description | Duration |",
            "|---|---|---|---|",
        ]
        for result in results:
            lines.append(
                f"| {_ICONS[result.outcome]} | `{result.nodeid}` "
                f"| {_escape(result.description)} "
                f"| {_format_duration(result.duration)} |"
            )
        failures = [
            result for result in results if result.outcome in ("failed", "error")
        ]
        if failures:
            lines += ["", "### Failures"]
        for result in failures:
            text = "\n\n".join(result.details).splitlines()[-MAX_FAILURE_LINES:]
            lines += [
                "",
                f"<details><summary><code>{result.nodeid}</code></summary>",
                "",
                "```",
                *text,
                "```",
                "",
                "</details>",
            ]
        return "\n".join(lines) + "\n\n"


def configure(config: pytest.Config) -> None:
    """Register the reporter if the job summary is requested."""
    path = os.environ.get(SUMMARY_ENV)
    # With pytest-xdist, only the controller writes the report.
    if not path or hasattr(config, "workerinput"):
        return
    title: str = config.getini("rugix_report_title")
    config.pluginmanager.register(SummaryReporter(Path(path), title), "rugix-report")


def _format_duration(seconds: float) -> str:
    minutes, seconds = divmod(round(seconds), 60)
    return f"{minutes}m {seconds}s" if minutes else f"{seconds}s"


def _escape(text: str) -> str:
    return text.replace("|", "\\|")
