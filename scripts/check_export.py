#!/usr/bin/env python3
"""Refuse to publish an export that carries anything personal.

`nhf export` leaves out the budget, the offices and the shortlist by
construction. This checks the result rather than trusting the construction,
because the cost of being wrong is asymmetric: a withheld number can be
published later, a published one cannot be unpublished. A template gaining
one `{{ cfg.budget }}` is all it would take, and that is a one-line change
nobody would think to re-examine.

Run as part of the publish workflow, before the upload step:

    python scripts/check_export.py dist

Exit status 1 means something personal reached the output. The values are
read from config/criteria.yml rather than hardcoded, so this keeps working
when those values change.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEXT_SUFFIXES = {".html", ".json", ".js", ".css", ".webmanifest", ".svg", ".txt"}


# The phrases criteria.py builds the price check out of. Each one either
# states the budget or hands over the subtraction that recovers it, and none
# of them can arise from anything else, so they are checked literally.
BUDGET_PHRASES = (
    "over budget",
    "tolerance is",
    "within $",
)


def forbidden_values() -> list[tuple[str, str]]:
    """Strings that must not appear anywhere in the export.

    Bare numbers are deliberately not on this list. The budget is a round
    figure and so are HDB prices, so a published comparable sale genuinely
    does hit $600,000 or $660,000 — the fixture export produced exactly that
    and a number scan called it a leak. Matching on numbers alone would train
    whoever runs this to ignore the result, which is worse than not checking.
    The budget number is instead reported only next to a word that frames it
    as a budget, in `near_budget_words`.
    """
    from nhf.config import load_config

    cfg = load_config()
    out: list[tuple[str, str]] = [
        (phrase, "the price criterion, which encodes the budget")
        for phrase in BUDGET_PHRASES
    ]

    for office in cfg.offices:
        out.append((office.postal, f"the postal code of {office.code}"))
        if office.label:
            out.append((office.label, f"the label of {office.code}"))

    return out


def near_budget_words(text: str, budget: int) -> bool:
    """True if the budget figure appears close to language that labels it.

    A sale price that coincides with the budget is noise; the same number
    within a few words of "budget" is a leak.
    """
    for spelling in (str(budget), f"{budget:,}"):
        for match in re.finditer(re.escape(spelling), text):
            window = text[max(0, match.start() - 80): match.end() + 80].lower()
            if "budget" in window:
                return True
    return False


def scan(target: Path) -> list[str]:
    from nhf.config import load_config

    problems: list[str] = []
    values = forbidden_values()
    budget = int(load_config().budget)

    files = [p for p in target.rglob("*") if p.is_file()]
    if not files:
        return [f"{target} has no files in it at all"]

    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            problems.append(f"could not read {path}: {exc}")
            continue
        for needle, why in values:
            if needle and needle in text:
                problems.append(
                    f"{path.relative_to(target)} contains {needle!r} ({why})"
                )
        if near_budget_words(text, budget):
            problems.append(
                f"{path.relative_to(target)} states the budget figure next to "
                "the word 'budget'"
            )

    # The shortlist is hand-entered and has no business being published.
    for path in files:
        name = path.name.lower()
        if name.startswith("listing") or name == "login.html":
            problems.append(f"{path.relative_to(target)} should not be published")

    # A price percentile would let the budget be solved for, even though the
    # budget itself is absent.
    candidates = target / "data" / "candidates.json"
    if candidates.exists():
        try:
            rows = json.loads(candidates.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"data/candidates.json is not valid JSON: {exc}")
        else:
            for row in rows[:500]:
                for sub in row.get("sub_scores", []):
                    if sub.get("key") == "price" and sub.get("percentile") is not None:
                        problems.append(
                            "data/candidates.json carries a scored price "
                            "sub-score, which encodes the budget"
                        )
                        break
                else:
                    continue
                break

    return problems


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else "dist")
    if not target.is_dir():
        print(f"no such directory: {target}")
        return 1

    problems = scan(target)
    if problems:
        print(f"refusing to publish {target}: {len(problems)} problem(s)")
        for problem in problems[:40]:
            print(f"  - {problem}")
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
        return 1

    print(f"{target} carries nothing personal: safe to publish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
