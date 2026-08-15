from __future__ import annotations

import math


def wilson_interval(successes: int, trials: int) -> tuple[float, float] | None:
    """Return a two-sided 95 percent Wilson score interval."""
    if trials == 0:
        return None
    z = 1.959963984540054
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (proportion + z * z / (2 * trials)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / trials + z * z / (4 * trials * trials)
        )
        / denominator
    )
    return center - margin, center + margin
