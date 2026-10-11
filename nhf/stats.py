"""The handful of statistics the scoring needs, without pulling in numpy."""

from __future__ import annotations

from typing import Iterable, Sequence


def median(values: Iterable[float]) -> float | None:
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    mid = len(data) // 2
    if len(data) % 2:
        return float(data[mid])
    return (data[mid - 1] + data[mid]) / 2.0


def percentile(values: Iterable[float], pct: float) -> float | None:
    """Linear-interpolated percentile, `pct` in 0..100."""
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    if len(data) == 1:
        return float(data[0])
    pos = (len(data) - 1) * (max(0.0, min(100.0, pct)) / 100.0)
    low = int(pos)
    high = min(low + 1, len(data) - 1)
    frac = pos - low
    return float(data[low] * (1 - frac) + data[high] * frac)


def percentile_rank(values: Sequence[float], value: float) -> float:
    """Where `value` sits in `values`, 0..1. Ties share the midpoint.

    This is how every growth signal is normalised before it is weighted, so
    signals measured in different units can be added up.
    """
    data = [v for v in values if v is not None]
    if not data:
        return 0.5
    below = sum(1 for v in data if v < value)
    equal = sum(1 for v in data if v == value)
    return (below + equal / 2.0) / len(data)


def cagr(start_value: float | None, end_value: float | None, years: float) -> float | None:
    """Compound annual growth rate, or None if it cannot be computed."""
    if not start_value or not end_value or years <= 0 or start_value <= 0 or end_value <= 0:
        return None
    return (end_value / start_value) ** (1.0 / years) - 1.0


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def linear_score(value: float | None, best: float, worst: float) -> float | None:
    """Map a value onto 0..1, where `best` scores 1 and `worst` scores 0.

    `best` may be below `worst` (a shorter commute is better), which is why
    the direction is given by the arguments rather than a flag.
    """
    if value is None or best == worst:
        return None
    return clamp((value - worst) / (best - worst))
