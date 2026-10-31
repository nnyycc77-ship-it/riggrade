"""Measurement arithmetic used by the rulers (spec section 5.2)."""


def percentile(values, pct):
    """Nearest-rank percentile, pct in (0, 100]."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * pct // 100))  # ceil
    return ordered[int(rank) - 1]


def decay(samples, window_s=300):
    """Throughput decay over a sustained run: first window against last.

    samples: list of (seconds since start, throughput). Returns the fractional
    drop of the last-window mean against the first-window mean (0.04 = 4%
    slower at the end), floored at 0. None if the run is shorter than two
    windows.
    """
    if not samples:
        return None
    end = samples[-1][0]
    if end < 2 * window_s:
        return None
    first = [v for t, v in samples if t <= window_s]
    last = [v for t, v in samples if t >= end - window_s]
    if not first or not last:
        return None
    a, b = sum(first) / len(first), sum(last) / len(last)
    if a <= 0:
        return None
    return max(0.0, (a - b) / a)


def p99_p50(latencies):
    p50, p99 = percentile(latencies, 50), percentile(latencies, 99)
    if not p50:
        return None
    return p99 / p50
