"""Intersected grid computation — pure helper, no DB dependencies.

The "rejilla intersectada" is the set of date ranges produced by taking the
union of all zone-boundary cut points from all providers and slicing the
requested date_range at each one.  Every time any provider changes zone, a new
row opens in the shared grid.
"""
from __future__ import annotations

from datetime import date, timedelta

from .dtos import ZoneRange


def compute_intersected_grid(
    zones_by_provider: dict[int, list[ZoneRange]],
    date_range: tuple[date, date],
) -> list[tuple[date, date]]:
    """Return the union-of-cut-points grid within date_range.

    Each returned (start, end) is inclusive on both ends.

    Example:
      Provider A: [(May 1 – Jul 14), (Jul 15 – Aug 31)]
      Provider B: [(May 1 – Jul 7),  (Jul 8  – Aug 31)]
      date_range:  (May 1, Aug 31)

      Result:
        [(May 1, Jul 7), (Jul 8, Jul 14), (Jul 15, Aug 31)]

    Returns [] when zones_by_provider contains no zones, or date_range is
    empty (d1 > d2).
    """
    d1, d2 = date_range
    if d1 > d2:
        return []

    all_zones = [z for zones in zones_by_provider.values() for z in zones]
    if not all_zones:
        return []

    end_marker = d2 + timedelta(days=1)
    cut_points: set[date] = {d1, end_marker}

    for zones in zones_by_provider.values():
        for zone in zones:
            # Zone start cut point, clamped to [d1, end_marker)
            s = max(zone.start_date, d1)
            if s < end_marker:
                cut_points.add(s)
            # Zone end cut point (one day after inclusive end), clamped to (d1, end_marker]
            e = min(zone.end_date + timedelta(days=1), end_marker)
            if e > d1:
                cut_points.add(e)

    sorted_points = sorted(cut_points)

    result: list[tuple[date, date]] = []
    for i in range(len(sorted_points) - 1):
        p = sorted_points[i]
        next_p = sorted_points[i + 1]
        if p >= end_marker:
            break
        result.append((p, next_p - timedelta(days=1)))

    return result
