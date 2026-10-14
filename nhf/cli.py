"""The `nhf` command line: the batch job, the checks, and the app."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path

from .config import DB_PATH, load_config
from .db import open_db

log = logging.getLogger("nhf")

STEPS = ["resale", "blocks", "price_index", "town_medians", "rentals",
         "schools", "future_mrt", "geocode", "commute",
         # Only by name. 174 MB of a plan revised every few years does not
         # belong in the monthly run, so `nhf sync` skips it.
         "land_use", "amenities", "rainfall"]


def _say(*parts) -> None:
    print(*parts, flush=True)


# --- commands -------------------------------------------------------------
def cmd_init(args) -> int:
    conn = open_db(args.db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    _say(f"store ready at {args.db or DB_PATH} with {len(tables)} tables")
    return 0


def cmd_sync(args) -> int:
    from .sync import run_sync

    conn = open_db(args.db)
    results = run_sync(conn, steps=args.step or None, limit=args.limit)
    failed = 0
    for entry in results:
        mark = {"ok": "ok", "skipped": "--", "error": "!!"}.get(entry["status"], "??")
        _say(f"[{mark}] {entry['dataset_key']:<22} {entry['rows'] or 0:>8} rows"
             f"{'  ' + entry['message'] if entry['message'] else ''}")
        failed += entry["status"] == "error"
    if failed:
        _say(f"\n{failed} step(s) failed. Nothing was guessed to fill the gap.")
    return 1 if failed else 0


def cmd_build(args) -> int:
    """Fit the curves and compute every cached number the app reads.

    Each step is logged like a sync step, so the Data page can show how old
    the scores are and not just how old the downloads are.
    """
    from .fit import fit_lease_factors, fit_storey_premiums
    from .growth import score_all
    from .metrics import compute_block_metrics
    from .sync import SyncReporter, derive_town_medians

    conn = open_db(args.db)
    if not conn.execute("SELECT 1 FROM resale_txn LIMIT 1").fetchone():
        _say("no transactions in the store: run `nhf sync` first")
        return 1

    reporter = SyncReporter(conn)
    steps: list[tuple[str, str, callable]] = []
    if not conn.execute("SELECT 1 FROM town_median WHERE source='hdb' LIMIT 1").fetchone():
        steps.append(("town_medians_derived", "town medians (derived)",
                      lambda: derive_town_medians(conn)))
    steps += [
        ("lease_factor", "fitted lease decay curve", lambda: fit_lease_factors(conn)),
        ("storey_premium", "fitted storey premium curve",
         lambda: fit_storey_premiums(conn)),
        ("block_metric", "per-block aggregates", lambda: compute_block_metrics(conn)),
        ("block_score", "growth scores and exit values", lambda: score_all(conn)),
    ]

    failed = 0
    for key, label, run in steps:
        try:
            rows = run()
        except Exception as exc:                       # keep going, report it
            reporter.record(key, "error", label=label, message=str(exc))
            _say(f"[!!] {label:<34} {exc}")
            failed += 1
            continue
        reporter.record(key, "ok" if rows else "skipped", rows=rows, label=label,
                        message=None if rows else "nothing to compute")
        _say(f"[{'ok' if rows else '--'}] {label:<34} {rows:>8} rows")
    return 1 if failed else 0


def cmd_fit(args) -> int:
    conn = open_db(args.db)
    _say("lease factor (all flat types) — value by years of lease left")
    rows = conn.execute(
        "SELECT lease_band, factor, n FROM lease_factor WHERE flat_type='ALL'"
        " ORDER BY lease_band"
    ).fetchall()
    for row in rows:
        bar = "#" * int(round(row["factor"] * 40))
        _say(f"  {row['lease_band']:>3}-{row['lease_band'] + 5:<3}y "
             f"{row['factor']:.3f} {bar} (n={row['n']})")
    _say("\nstorey premium (all flat types) — value by storey band")
    for row in conn.execute(
        "SELECT storey_band, factor, n FROM storey_premium WHERE flat_type='ALL'"
        " ORDER BY storey_band"
    ):
        bar = "#" * int(round(row["factor"] * 40))
        _say(f"  {row['storey_band']:>3}-{row['storey_band'] + 4:<3}  "
             f"{row['factor']:.3f} {bar} (n={row['n']})")
    if not rows:
        _say("  nothing fitted yet: run `nhf build`")
    return 0


def cmd_rank(args) -> int:
    from .ranking import ranked_list

    conn = open_db(args.db)
    rows = ranked_list(
        conn,
        towns=args.town or None,
        flat_types=args.flat_type or None,
        max_price=args.max_price,
    )
    if not rows:
        _say("no candidates: run `nhf sync` then `nhf build`")
        return 1
    _say(f"{len(rows)} candidates; top {min(args.top, len(rows))}\n")
    _say(f"{'rank':>5} {'blk':>5} {'street':<22} {'type':<8} {'median':>9} "
         f"{'grw':>4} {'lease':>6} {'drive':>6}  flags")
    def cell(value, width: int, places: int = 1, comma: bool = False) -> str:
        """An unknown number reads as unknown, not as zero.

        `or 0` printed a block with no routed drive time as 0.0 minutes from
        the office — the one thing this app is supposed never to do, and the
        more convincing for sitting in a column of real numbers.
        """
        if value is None:
            return f"{'—':>{width}}"
        return f"{value:>{width},.{places}f}" if comma else f"{value:>{width}.{places}f}"

    for c in rows[:args.top]:
        flags = "; ".join(m.detail for m in (c.fit.misses if c.fit else []))
        _say(f"{cell(c.rank_score, 5)} {c.block:>5} {c.street_name[:22].title():<22} "
             f"{c.flat_type:<8} {cell(c.median_price_24m, 9, 0, comma=True)} "
             f"{cell(c.growth_score, 4, 0)} {cell(c.lease_years_at_exit, 6)} "
             f"{cell(c.drive_minutes, 6)}  {flags}")
    return 0


def cmd_value(args) -> int:
    from .fairvalue import Valuer

    conn = open_db(args.db)
    valuer = Valuer(conn)
    estimate = valuer.estimate(
        args.block_key, args.flat_type, storey=args.storey, floor_area_sqm=args.sqm
    )
    if not estimate.available:
        _say("no fair-value range: " + "; ".join(estimate.notes))
        return 1
    _say(f"{args.block_key} {args.flat_type}"
         f"{f', storey {args.storey}' if args.storey else ''}"
         f"{f', {args.sqm:.0f} sqm' if args.sqm else ''}")
    _say(f"  low  {estimate.low:>10,.0f}")
    _say(f"  mid  {estimate.mid:>10,.0f}   (${estimate.psm_mid:,.0f}/sqm)")
    _say(f"  high {estimate.high:>10,.0f}")
    _say(f"  from {estimate.n_comps} comparable sales ({estimate.scope.replace('_', ' ')})")
    for note in estimate.notes:
        _say(f"  note: {note}")
    if args.asking:
        _say(f"  asking {args.asking:,.0f} is {estimate.tag(args.asking)} the range")
    return 0


def cmd_backtest(args) -> int:
    from .backtest import run_backtest

    conn = open_db(args.db)
    overrides = None
    if args.weight:
        overrides = dict(load_config().growth_weights)
        for pair in args.weight:
            key, _, value = pair.partition("=")
            if key not in overrides:
                _say(f"unknown signal {key!r}; known: {', '.join(overrides)}")
                return 1
            overrides[key] = float(value)
    result = run_backtest(
        conn, as_of=args.as_of, horizon_years=args.horizon, weights=overrides
    )
    _say(f"scored as of {result.as_of}, graded over {result.horizon_years} years, "
         f"{result.n_blocks} blocks")
    _say("signals used: " + ", ".join(
        f"{k}={v:g}" for k, v in result.weights.items() if v) or "none")
    if result.quintiles:
        _say(f"\n{'quintile':>8} {'n':>5} {'median score':>13} {'realised growth':>16}")
        for q in result.quintiles:
            _say(f"{q.rank:>8} {q.n:>5} {q.median_score or 0:>13.1f} "
                 f"{(q.median_growth or 0) * 100:>15.2f}%")
    _say("\n" + result.message)
    if result.passed is False:
        return 2
    return 0 if result.passed else 1


def cmd_geocode(args) -> int:
    from .sources.onemap import OneMapClient
    from .sync import (
        SyncReporter, geocode_amenities, geocode_blocks, geocode_offices,
    )

    conn = open_db(args.db)
    cfg = load_config()
    reporter = SyncReporter(conn)
    client = OneMapClient()
    picked = args.offices or args.blocks or args.schools
    if args.offices or not picked:
        _say(f"offices geocoded: {geocode_offices(conn, cfg, reporter, client)}")
    if args.blocks or not picked:
        _say(f"blocks geocoded: "
             f"{geocode_blocks(conn, reporter, client, limit=args.limit)}")
    if args.schools or not picked:
        _say(f"schools geocoded: "
             f"{geocode_amenities(conn, reporter, client, 'school', limit=args.limit)}")
    for entry in reporter.results:
        if entry["message"]:
            _say(f"  {entry['dataset_key']}: {entry['message']}")
    return 0


def cmd_commute(args) -> int:
    from .sources.onemap import OneMapClient
    from .sync import SyncReporter, compute_commute

    conn = open_db(args.db)
    reporter = SyncReporter(conn)
    written = compute_commute(
        conn, load_config(), reporter, OneMapClient(), limit=args.limit,
        only_missing=not args.all,
    )
    _say(f"routed {written} block-office legs")
    for entry in reporter.results:
        if entry["message"]:
            _say(f"  {entry['message']}")
    return 0 if written else 1


def cmd_import_mrt(args) -> int:
    """Load existing MRT stations from an LTA GeoJSON download.

    No open station-level API was found, so this takes a file: the LTA train
    station layer from data.gov.sg, or any GeoJSON of points with a name.
    """
    from .db import upsert_many

    conn = open_db(args.db)
    payload = json.loads(Path(args.path).read_text())
    rows = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point":
            continue
        lon, lat = geometry["coordinates"][:2]
        props = {k.lower(): v for k, v in (feature.get("properties") or {}).items()}
        name = props.get("name") or props.get("station_na") or props.get("stn_name")
        if not name:
            continue
        rows.append((
            str(name).upper().strip(), str(props.get("line") or props.get("rail_line") or ""),
            "open", None, lat, lon, f"import:{Path(args.path).name}",
        ))
    with conn:
        upsert_many(
            conn, "mrt_station",
            ("name", "line", "status", "expected_year", "lat", "lon", "source"),
            rows, conflict="REPLACE",
        )
    _say(f"imported {len(rows)} stations from {args.path}")
    return 0 if rows else 1


def cmd_geocode_mrt(args) -> int:
    """Fill coordinates for the hand-kept future stations, via OneMap."""
    from .sources.onemap import OneMapClient

    conn = open_db(args.db)
    client = OneMapClient()
    pending = conn.execute(
        "SELECT name, line, status FROM mrt_station WHERE lat IS NULL"
    ).fetchall()
    done = 0
    for row in pending:
        for query in (f"{row['name']} MRT STATION", row["name"]):
            try:
                point = client.geocode(query)
            except Exception as exc:
                _say(f"  {row['name']}: {exc}")
                point = None
            if point:
                conn.execute(
                    "UPDATE mrt_station SET lat=?, lon=? WHERE name=? AND line=? AND status=?",
                    (point.lat, point.lon, row["name"], row["line"], row["status"]),
                )
                conn.commit()
                done += 1
                break
        else:
            _say(f"  {row['name']}: no match; paste lat/lon into config/future_mrt.yml")
    _say(f"geocoded {done} of {len(pending)} stations")
    return 0


def cmd_status(args) -> int:
    from .web.app import freshness

    conn = open_db(args.db)
    rows = freshness(conn)
    if not rows:
        _say("nothing synced yet")
        return 1
    _say(f"{'dataset':<24} {'status':<8} {'rows':>9} {'age':>6}")
    stale = 0
    for row in rows:
        age = f"{row['age_days']}d" if row["age_days"] is not None else "—"
        _say(f"{row['dataset_key']:<24} {row['status']:<8} {row['rows'] or 0:>9,} {age:>6}")
        stale += bool(row["stale"])
    counts = {
        table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in ("resale_txn", "block", "block_metric", "block_score", "listing")
    }
    _say("\n" + "  ".join(f"{k}={v:,}" for k, v in counts.items()))
    if stale:
        _say(f"\n{stale} dataset(s) older than 45 days")
    return 0


def cmd_export(args) -> int:
    """Write the read-only app out as static files, for a host that serves files."""
    from .export import export_site

    conn = open_db(args.db)
    if not conn.execute("SELECT 1 FROM block_metric LIMIT 1").fetchone():
        _say("no block metrics in the store: run `nhf sync` then `nhf build` first")
        return 1

    report = export_site(
        conn, args.out,
        freeze_blocks=not args.no_blocks,
        max_pages=args.max_pages,
        limit=args.limit,
    )
    _say(f"wrote {report.candidates} candidates and {report.pages} block page(s) "
         f"to {report.out_dir} ({report.bytes_written / 1_000_000:.1f} MB)")
    _say("left out: the budget and its price sub-score, both offices and their "
         "drive times, and the shortlist")
    for warning in report.warnings:
        _say(f"[!!] {warning}")
    return 0


def cmd_serve(args) -> int:
    from .web.app import create_app
    from .web.auth import auth_disabled

    app = create_app(args.db)
    if auth_disabled():
        _say("NHF_USERS is not set, so only localhost is served.")
        _say("Make accounts with: nhf hash-password")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


def cmd_hash_password(args) -> int:
    from .web.auth import hash_password

    name = args.name or input("name: ").strip()
    password = getpass.getpass("password: ")
    if not password:
        _say("no password given")
        return 1
    _say(f"\nAdd this to NHF_USERS (comma-separate several):\n\n"
         f"{name.lower()}:{hash_password(password)}\n")
    return 0


# --- parser ---------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nhf", description="Next Home Finder — HDB resale shortlist and scoring",
    )
    parser.add_argument("--db", help=f"SQLite file (default {DB_PATH})")
    parser.add_argument("-v", "--verbose", action="store_true")
    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser("init", help="create the store").set_defaults(func=cmd_init)

    sync = subs.add_parser("sync", help="download every dataset (monthly job)")
    sync.add_argument("--step", action="append", choices=STEPS,
                      help="run only this step; repeatable")
    sync.add_argument("--limit", type=int, help="stop after N records per dataset")
    sync.set_defaults(func=cmd_sync)

    subs.add_parser("build", help="fit curves, compute metrics and scores"
                    ).set_defaults(func=cmd_build)
    subs.add_parser("fit", help="print the fitted lease and storey curves"
                    ).set_defaults(func=cmd_fit)

    rank = subs.add_parser("rank", help="print the ranked list")
    rank.add_argument("--top", type=int, default=20)
    rank.add_argument("--town", action="append")
    rank.add_argument("--flat-type", action="append", dest="flat_type")
    rank.add_argument("--max-price", type=float, dest="max_price")
    rank.set_defaults(func=cmd_rank)

    value = subs.add_parser("value", help="fair value for one unit")
    value.add_argument("block_key", help='e.g. "101|ANG MO KIO AVE 3"')
    value.add_argument("flat_type")
    value.add_argument("--storey", type=int)
    value.add_argument("--sqm", type=float)
    value.add_argument("--asking", type=float)
    value.set_defaults(func=cmd_value)

    back = subs.add_parser("backtest", help="grade the growth score on history")
    back.add_argument("--as-of", default="2019-12", dest="as_of")
    back.add_argument("--horizon", type=int, default=5)
    back.add_argument("--weight", action="append", metavar="SIGNAL=N",
                      help="try a different weighting, e.g. --weight catch_up_gap=0")
    back.set_defaults(func=cmd_backtest)

    geo = subs.add_parser("geocode", help="geocode offices and blocks via OneMap")
    geo.add_argument("--offices", action="store_true")
    geo.add_argument("--blocks", action="store_true")
    geo.add_argument("--schools", action="store_true",
                     help="place MOE schools from their postal codes")
    geo.add_argument("--limit", type=int)
    geo.set_defaults(func=cmd_geocode)

    commute = subs.add_parser("commute", help="route drive times to both offices")
    commute.add_argument("--limit", type=int)
    commute.add_argument("--all", action="store_true", help="recompute, not just missing")
    commute.set_defaults(func=cmd_commute)

    mrt = subs.add_parser("import-mrt", help="load existing MRT stations from GeoJSON")
    mrt.add_argument("path")
    mrt.set_defaults(func=cmd_import_mrt)

    subs.add_parser("geocode-mrt", help="fill coordinates for future stations"
                    ).set_defaults(func=cmd_geocode_mrt)
    subs.add_parser("status", help="data freshness summary").set_defaults(func=cmd_status)

    export = subs.add_parser(
        "export", help="write a read-only static copy (for GitHub Pages)")
    export.add_argument("--out", default="dist", help="output directory")
    export.add_argument("--no-blocks", action="store_true",
                        help="skip the per-block pages (much faster)")
    export.add_argument("--max-pages", type=int,
                        help="cap on block pages; the rest are listed unlinked")
    export.add_argument("--limit", type=int,
                        help="only the top N candidates, for a quick check")
    export.set_defaults(func=cmd_export)

    serve = subs.add_parser("serve", help="run the phone app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--debug", action="store_true")
    serve.set_defaults(func=cmd_serve)

    hash_cmd = subs.add_parser("hash-password", help="make an NHF_USERS entry")
    hash_cmd.add_argument("name", nargs="?")
    hash_cmd.set_defaults(func=cmd_hash_password)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
