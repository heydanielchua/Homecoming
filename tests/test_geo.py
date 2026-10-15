"""Simplifying a parcel outline enough to publish it.

The Master Plan draws boundaries to fourteen decimal places, which is a tenth
of a millimetre, and there are 113,212 of them. What gets published is about
five metres of that. These pin down the two ways that goes wrong: throwing
away a shape that was only small, and filling in a hole that was real.
"""

from __future__ import annotations

from nhf.geo import OUTLINE_TOLERANCE_M, outline, simplify_ring


def ring(corners: list[tuple[float, float]], per_edge: int = 25) -> list:
    """A closed ring with redundant points along every edge."""
    out: list = []
    for i, start in enumerate(corners):
        end = corners[(i + 1) % len(corners)]
        for step in range(per_edge):
            t = step / per_edge
            out.append([
                start[0] + (end[0] - start[0]) * t,
                start[1] + (end[1] - start[1]) * t,
            ])
    out.append(list(out[0]))
    return out


def square(lon: float, lat: float, side_deg: float) -> list:
    return ring([
        (lon, lat),
        (lon + side_deg, lat),
        (lon + side_deg, lat + side_deg),
        (lon, lat + side_deg),
    ])


class TestSimplifyRing:
    def test_a_straight_edge_keeps_its_corners_and_nothing_else(self):
        """101 points around a 100 m square is four corners and the close."""
        assert len(simplify_ring(square(103.80, 1.30, 0.0009))) == 5

    def test_a_ring_too_short_to_simplify_is_returned_as_it_is(self):
        tiny = [[103.8, 1.3], [103.8001, 1.3], [103.8, 1.3]]
        assert simplify_ring(tiny) == tiny

    def test_a_detour_wider_than_the_tolerance_survives(self):
        """A notch 20 m deep is a real feature at a 5 m tolerance."""
        notched = [
            [103.80, 1.30], [103.801, 1.30],
            [103.801, 1.3002], [103.8008, 1.3002],
            [103.8008, 1.3001], [103.80, 1.3001], [103.80, 1.30],
        ]
        assert len(simplify_ring(notched, 5.0)) == len(notched)


class TestOutline:
    def test_it_keeps_a_hole(self):
        """A parcel wrapped round a reservoir is a real hole. Filling it in
        draws land over water, which a reader checks against the window."""
        outer = square(103.80, 1.30, 0.0018)
        hole = square(103.8006, 1.3006, 0.0006)
        shape = outline({"type": "Polygon", "coordinates": [outer, hole]})
        assert shape["type"] == "Polygon"
        assert len(shape["coordinates"]) == 2

    def test_a_parcel_smaller_than_the_tolerance_is_still_drawn(self):
        """The bug this exists for: at a flat 5 m tolerance an 8 m plot
        simplifies to nothing, and 4,331 residential parcels vanished — which
        shows up as holes in the ground wash, not as an error."""
        small = square(103.80, 1.30, 0.00008)      # about 9 m a side
        assert outline(small and {"type": "Polygon", "coordinates": [small]})

    def test_the_tolerance_is_what_it_claims_to_be(self):
        """Five metres, and the test says so rather than trusting the name."""
        assert OUTLINE_TOLERANCE_M == 5.0

    def test_coordinates_are_rounded_to_about_a_metre(self):
        shape = outline({"type": "Polygon", "coordinates": [square(103.8, 1.3, 0.0009)]})
        for lon, lat in shape["coordinates"][0]:
            assert len(str(lon).split(".")[-1]) <= 5
            assert len(str(lat).split(".")[-1]) <= 5

    def test_a_multipolygon_stays_one(self):
        a = square(103.80, 1.30, 0.0009)
        b = square(103.81, 1.31, 0.0009)
        shape = outline({"type": "MultiPolygon", "coordinates": [[a], [b]]})
        assert shape["type"] == "MultiPolygon"
        assert len(shape["coordinates"]) == 2

    def test_a_line_is_not_a_polygon(self):
        """Degenerate rings are dropped rather than drawn as a sliver: a
        parcel drawn wrong is worse than a parcel not drawn."""
        line = [[103.8, 1.3], [103.9, 1.3], [103.8, 1.3]]
        assert outline({"type": "Polygon", "coordinates": [line]}) is None

    def test_a_point_has_no_outline(self):
        assert outline({"type": "Point", "coordinates": [103.8, 1.3]}) is None

    def test_nothing_in_nothing_out(self):
        assert outline({}) is None
        assert outline({"type": "Polygon", "coordinates": []}) is None
