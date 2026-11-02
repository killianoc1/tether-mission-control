"""Georeferencing helpers: turn grid cells into real-world WGS 84 geometry.

The grid is laid out on a local flat-Earth approximation anchored at the grid's
south-west corner.  Over a few hundred metres the error is far below one cell,
so this is exact enough for a visual overlay (it is *not* survey-grade).

Grid convention: ``x`` increases east, ``y`` increases north, cell (0, 0) is the
south-west cell.
"""
import math

from django.contrib.gis.geos import Point, Polygon

METRES_PER_DEG_LAT = 111_320.0
SRID = 4326


def _deg_per_metre(lat: float) -> tuple[float, float]:
    """Return (degrees longitude per metre, degrees latitude per metre) at ``lat``."""
    return 1.0 / (METRES_PER_DEG_LAT * math.cos(math.radians(lat))), 1.0 / METRES_PER_DEG_LAT


def cell_corners(origin_lng: float, origin_lat: float, cell_size_m: float, x: int, y: int):
    """(west, south, east, north) of cell ``(x, y)``."""
    dlng, dlat = _deg_per_metre(origin_lat)
    west = origin_lng + x * cell_size_m * dlng
    south = origin_lat + y * cell_size_m * dlat
    return west, south, west + cell_size_m * dlng, south + cell_size_m * dlat


def cell_centre(origin_lng: float, origin_lat: float, cell_size_m: float, x: int, y: int) -> Point:
    """Real-world point at the middle of cell (x, y); used for base, target and every route step."""
    w, s, e, n = cell_corners(origin_lng, origin_lat, cell_size_m, x, y)
    return Point((w + e) / 2, (s + n) / 2, srid=SRID)


def cell_polygon(origin_lng: float, origin_lat: float, cell_size_m: float, x: int, y: int) -> Polygon:
    """Square polygon covering cell (x, y); used for obstacle and hazard cells."""
    w, s, e, n = cell_corners(origin_lng, origin_lat, cell_size_m, x, y)
    return _bbox_polygon(w, s, e, n)


def _bbox_polygon(w: float, s: float, e: float, n: float) -> Polygon:
    """Rectangle from west/south/east/north edges; the ring is closed by repeating the first point."""
    return Polygon(((w, s), (e, s), (e, n), (w, n), (w, s)), srid=SRID)


def grid_extent(origin_lng: float, origin_lat: float, cell_size_m: float, size: int) -> Polygon:
    """Polygon covering the whole ``size`` x ``size`` grid."""
    w, s, _, _ = cell_corners(origin_lng, origin_lat, cell_size_m, 0, 0)
    _, _, e, n = cell_corners(origin_lng, origin_lat, cell_size_m, size - 1, size - 1)
    return _bbox_polygon(w, s, e, n)


def origin_for_base(base_lng: float, base_lat: float, cell_size_m: float, base_x: int, base_y: int):
    """South-west grid corner such that the *centre* of the base cell is at (base_lng, base_lat)."""
    dlng, dlat = _deg_per_metre(base_lat)
    return (
        base_lng - (base_x + 0.5) * cell_size_m * dlng,
        base_lat - (base_y + 0.5) * cell_size_m * dlat,
    )
