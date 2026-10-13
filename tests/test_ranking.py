"""F5: the weighted rank, and the promise that it is explainable."""

import pytest

from nhf.ranking import load_candidates, rank, ranked_list


def test_candidates_are_loaded_with_their_data(fitted_db):
    candidates = load_candidates(fitted_db)
    assert candidates
    first = candidates[0]
    assert first.block and first.street_name and first.town
    assert first.median_price_24m is not None
    assert first.drive_minutes is not None            # the fixture has routed times
    assert first.nearest_mrt_m is not None


def test_price_filter_is_a_wide_net_on_the_cheapest_sale(fitted_db):
    tight = load_candidates(fitted_db, max_price=400000)
    wide = load_candidates(fitted_db, max_price=900000)
    assert len(tight) < len(wide)
    assert all(c.min_price_24m <= 400000 for c in tight if c.min_price_24m)


def test_small_flat_types_are_dropped_from_the_candidate_net(fitted_db):
    fitted_db.execute(
        "INSERT OR REPLACE INTO block_metric (block_key, flat_type, median_price_24m,"
        " min_price_24m) VALUES ('141|TAMPINES ST 42', '2 ROOM', 300000, 300000)"
    )
    fitted_db.commit()
    types = {c.flat_type for c in load_candidates(fitted_db)}
    assert "2 ROOM" not in types
    assert "2 ROOM" in {c.flat_type for c in load_candidates(fitted_db, min_rooms=0)}


def test_ranking_is_sorted_and_scored(fitted_db):
    rows = ranked_list(fitted_db)
    scores = [c.rank_score for c in rows if c.rank_score is not None]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 100 for s in scores)


def test_every_rank_shows_its_inputs(fitted_db):
    candidate = ranked_list(fitted_db)[0]
    keys = {s.key for s in candidate.sub_scores}
    assert keys == {"price", "lease", "growth", "commute", "amenities"}
    for sub in candidate.sub_scores:
        assert sub.unit
        if sub.percentile is None:
            assert sub.note                      # an absence must be explained
    assert candidate.fit is not None


def test_weights_change_the_order(fitted_db):
    by_price = ranked_list(fitted_db, weights={"price": 100})
    by_lease = ranked_list(fitted_db, weights={"lease": 100})
    assert by_price[0].block_key != by_lease[0].block_key
    # Scoring on price alone, the cheapest block must come first. The price
    # scale plateaus below 85% of budget, so this is the tie-break doing its
    # job rather than the scale.
    cheapest = min(
        (c for c in by_price if c.median_price_24m), key=lambda c: c.median_price_24m
    )
    assert by_price[0].median_price_24m == cheapest.median_price_24m
    top_note = next(s for s in by_price[0].sub_scores if s.key == "price").note
    assert "comfortably within budget" in top_note


def test_scores_are_absolute_so_filtering_does_not_reshuffle(fitted_db):
    """A rank must not move because something else was filtered out."""
    everything = {c.block_key + c.flat_type: c.rank_score for c in ranked_list(fitted_db)}
    narrowed = ranked_list(fitted_db, towns=["TAMPINES"])
    assert narrowed
    for c in narrowed:
        if c.rank_score is not None:
            assert c.rank_score == pytest.approx(everything[c.block_key + c.flat_type])


def test_a_missing_sub_score_does_not_punish_a_block(fitted_db):
    """A block with no routed drive time keeps its score on the rest."""
    fitted_db.execute("DELETE FROM commute WHERE block_key = '141|TAMPINES ST 42'")
    fitted_db.commit()
    rows = {c.block_key: c for c in ranked_list(fitted_db) if c.flat_type == "4 ROOM"}
    target = rows["141|TAMPINES ST 42"]
    commute = next(s for s in target.sub_scores if s.key == "commute")
    assert commute.percentile is None
    assert commute.note
    assert target.rank_score is not None


def test_flagged_candidates_are_still_in_the_list(fitted_db):
    rows = ranked_list(fitted_db)
    assert any(c.fit.misses for c in rows), "nothing was flagged, so the test is blind"
    far_north = [c for c in rows if c.town in ("WOODLANDS", "YISHUN", "SEMBAWANG")]
    assert far_north, "far-north blocks must still appear, flagged"
    assert all(any(m.key == "location" for m in c.fit.misses) for c in far_north)


def test_candidate_serialises_for_the_api(fitted_db):
    data = ranked_list(fitted_db)[0].to_dict()
    assert data["fit"]["checks"]
    assert data["sub_scores"]
    assert "growth_signals" in data


def test_loading_one_block_scores_it_exactly_as_loading_the_list_does(fitted_db):
    """The block page loads a single block when it is being published.

    That is only safe because four sub-scores are on fixed scales and the
    growth score is read back from the store, so nothing a candidate scores
    depends on which other candidates were loaded. If that ever stops being
    true, this fails.
    """
    from nhf.ranking import attach_fit, load_candidates, rank

    everything = rank(load_candidates(fitted_db, max_price=None, min_rooms=0), None)
    assert everything, "fixture produced no candidates"

    for want in everything[:6]:
        alone = rank(
            load_candidates(fitted_db, max_price=None, min_rooms=0,
                            block_keys=[want.block_key]),
            None,
        )
        got = next(c for c in alone
                   if (c.block_key, c.flat_type) == (want.block_key, want.flat_type))
        attach_fit(fitted_db, [got])
        attach_fit(fitted_db, [want])
        assert got.to_dict() == want.to_dict()


def test_block_keys_filter_returns_only_those_blocks(fitted_db):
    from nhf.ranking import load_candidates

    everything = load_candidates(fitted_db, max_price=None, min_rooms=0)
    target = everything[0].block_key
    narrowed = load_candidates(fitted_db, max_price=None, min_rooms=0,
                               block_keys=[target])
    assert narrowed, "the filter should not empty the list"
    assert {c.block_key for c in narrowed} == {target}
