"""Fill docs/EVALUATION.md's tables from the generated report (no hand-copied numbers).

``docs/EVALUATION.template.md`` holds the prose with markers such as ``{{compact:main}}``. This
module replaces each marker with the matching table, rebuilt from the saved experiment outputs by
the same functions that write ``docs/results/RESULTS_TABLES.md``, and writes ``docs/EVALUATION.md``.
Run ``python -m scalix.evaluation_doc`` after ``python -m scalix.evaluation report``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from scalix import evaluation as ev
from scalix.config import PROJECT_ROOT

TEMPLATE = PROJECT_ROOT / "docs" / "EVALUATION.template.md"
OUTPUT = PROJECT_ROOT / "docs" / "EVALUATION.md"
MARKER = re.compile(r"\{\{([a-z_]+)(?::([a-z0-9.\-]+))?\}\}")


def _body(table: str) -> str:
    """Drop a generated section's heading lines; keep its explanation and the table."""
    lines = table.strip().splitlines()
    while lines and (lines[0].startswith("#") or not lines[0].strip()):
        lines.pop(0)
    return "\n".join(lines)


def render(template: str, datasets: dict[str, dict]) -> str:
    def fill(m: re.Match) -> str:
        kind, arg = m.group(1), m.group(2)
        if kind == "compact":
            return _body(ev.compact_table(datasets[arg], arg))
        if kind == "strategy_paired":
            return _body(ev.strategy_paired_table(datasets))
        if kind == "strategy_paired_tt":
            return _body(ev.strategy_paired_table(datasets, "TARGET_TRACKING"))
        if kind == "paired":
            return _body(ev.paired_table(datasets))
        if kind == "slo":
            return _body(ev.slo_table(datasets))
        if kind == "holds":
            return _body(ev.scale_down_table(datasets["main"]["holds"]))
        raise KeyError(f"unknown marker {m.group(0)}")
    return MARKER.sub(fill, template)


def main() -> int:
    datasets = {n: ev.load(ev.RESULTS_ROOT, n) for n in ev.SUITE if (ev.RESULTS_ROOT / n / "summary.csv").exists()}
    OUTPUT.write_text(render(TEMPLATE.read_text(encoding="utf-8"), datasets), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
