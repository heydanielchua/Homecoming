"""The phone app.

Mobile first, server-rendered, installable to the home screen. It reads only
from the local SQLite store, so nothing here waits on a government API, and
it shows how old that data is on every page.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask, abort, flash, g, jsonify, redirect, render_template, request,
    send_from_directory, session, url_for,
)
from werkzeug.utils import secure_filename

from ..config import DATA_DIR, load_config
from ..criteria import evaluate
from ..db import open_db
from ..fairvalue import Valuer
from ..growth import retirement_rent_check
from ..norm import canon_flat_type
from ..ranking import attach_fit, load_candidates, rank
from .auth import SESSION_KEY, auth_disabled, check_login, current_user, login_required

PHOTO_DIR = Path(os.environ.get("NHF_PHOTO_DIR", DATA_DIR / "photos"))
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
ALLOWED_PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

STATUSES = ["Watching", "Viewed", "Offered", "Dropped"]

# F7: the checklist covers what government data cannot.
VIEWING_CHECKLIST = [
    ("corner", "Is it a corner unit?"),
    ("facing", "Which way does it face?"),
    ("view", "What do the windows look onto?"),
    ("hardcourt", "Any hard court or other noise source in sight?"),
    ("afternoon_sun", "How bad is the afternoon sun?"),
    ("lift", "How far to the lift and the carpark?"),
    ("condition", "Condition: what would need redoing?"),
    ("eip", "EIP / SPR quota checked with HDB for this block?"),
]


# --- app ------------------------------------------------------------------
def create_app(db_path: str | Path | None = None, conn: sqlite3.Connection | None = None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("NHF_SECRET_KEY", os.urandom(32).hex()),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 90,
        NHF_DB_PATH=str(db_path) if db_path else None,
        NHF_TEST_CONN=conn,
    )

    def get_db() -> sqlite3.Connection:
        if app.config["NHF_TEST_CONN"] is not None:
            return app.config["NHF_TEST_CONN"]
        if "db" not in g:
            g.db = open_db(app.config["NHF_DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    # --- template helpers -------------------------------------------------
    @app.template_filter("money")
    def money(value):
        return "—" if value is None else f"${value:,.0f}"

    @app.template_filter("num")
    def num(value, places=1):
        return "—" if value is None else f"{value:,.{places}f}"

    @app.template_filter("pct")
    def pct(value, places=1):
        return "—" if value is None else f"{value * 100:,.{places}f}%"

    @app.template_filter("month_name")
    def month_name(value):
        if not value:
            return "—"
        try:
            return datetime.strptime(value, "%Y-%m").strftime("%b %Y")
        except ValueError:
            return value

    @app.context_processor
    def inject_globals():
        cfg = load_config()
        return {
            "cfg": cfg,
            "user": current_user(),
            "auth_disabled": auth_disabled(),
            "freshness": freshness(get_db()),
            "onemap_tiles": cfg.onemap("tile_url"),
            "onemap_attribution": cfg.onemap("attribution"),
            "statuses": STATUSES,
            # Set by `nhf export`: the page is going to a static host, where
            # there is nothing to post to and no session to sign out of.
            "static_export": app.config.get("NHF_STATIC_EXPORT", False),
        }

    # --- auth -------------------------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if auth_disabled():
            return redirect(url_for("index"))
        error = None
        if request.method == "POST":
            name = request.form.get("name", "")
            if check_login(name, request.form.get("password", "")):
                session.permanent = True
                session[SESSION_KEY] = name.strip().lower()
                return redirect(request.args.get("next") or url_for("index"))
            error = "That name and password did not match."
        return render_template("login.html", error=error)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.pop(SESSION_KEY, None)
        return redirect(url_for("login"))

    # --- ranked list ------------------------------------------------------
    @app.route("/")
    @login_required
    def index():
        db = get_db()
        weights, filters = read_controls(request.args)
        candidates = rank(attach_fit(db, load_candidates(db, **filters)), weights)
        hide_flagged = request.args.get("hide_flagged") == "1"
        shown = [c for c in candidates if not (hide_flagged and c.fit and c.fit.misses)]
        return render_template(
            "index.html",
            candidates=shown[:200],
            total=len(candidates),
            weights=weights,
            filters=filters,
            hide_flagged=hide_flagged,
            towns=all_towns(db),
            flat_types=all_flat_types(db),
            empty=not candidates,
        )

    # --- block profile (F2) ----------------------------------------------
    @app.route("/block/<path:block_key>/<flat_type>")
    @login_required
    def block_profile(block_key: str, flat_type: str):
        db = get_db()
        flat_type = canon_flat_type(flat_type)
        block = db.execute("SELECT * FROM block WHERE block_key = ?", (block_key,)).fetchone()
        if not block:
            abort(404)

        weights, _ = read_controls(request.args)
        # The whole list is scored because this page shows where the block
        # places in it, but only this block's criteria are displayed, and
        # ranking does not read them: grading all 14,000 cost about 9,000
        # evaluate() calls per page view, which is most of the render.
        #
        # A published page states no rank at all — it would be computed from
        # whichever weights were in force at build time — so there it loads
        # this block alone and skips the list entirely.
        if app.config.get("NHF_STATIC_EXPORT"):
            candidates = rank(
                load_candidates(db, max_price=None, min_rooms=0,
                                block_keys=[block_key]),
                weights,
            )
            rank_position, of_total = None, None
        else:
            candidates = rank(load_candidates(db, max_price=None, min_rooms=0), weights)
            of_total = len(candidates)
        candidate = next(
            (c for c in candidates
             if c.block_key == block_key and c.flat_type == flat_type), None
        )
        if not app.config.get("NHF_STATIC_EXPORT"):
            rank_position = (candidates.index(candidate) + 1) if candidate else None
        if candidate is not None:
            attach_fit(db, [candidate])

        storey = request.args.get("storey", type=int)
        sqm = request.args.get("sqm", type=float)
        asking = request.args.get("asking", type=float)
        valuer = Valuer(db)
        estimate = valuer.estimate(block_key, flat_type, storey=storey, floor_area_sqm=sqm)

        return render_template(
            "block.html",
            block=block,
            flat_type=flat_type,
            candidate=candidate,
            rank_position=rank_position,
            of_total=of_total,
            estimate=estimate,
            asking=asking,
            asking_tag=estimate.tag(asking),
            storey=storey,
            transactions=recent_transactions(db, block_key),
            by_type=medians_by_type(db, block_key),
            by_storey=medians_by_storey(db, block_key, flat_type),
            trend=price_trend(db, block_key, flat_type, block["town"]),
            stations=nearby_stations(db, block),
            rent=retirement_rent_check(db, block_key, flat_type),
            flat_types_here=flat_types_in_block(db, block_key),
            weights=weights,
        )

    # --- map (F6) ---------------------------------------------------------
    @app.route("/map")
    @login_required
    def map_view():
        weights, filters = read_controls(request.args)
        return render_template("map.html", weights=weights, filters=filters)

    @app.route("/api/candidates.json")
    @login_required
    def candidates_json():
        db = get_db()
        weights, filters = read_controls(request.args)
        candidates = rank(attach_fit(db, load_candidates(db, **filters)), weights)
        return jsonify([
            {
                "block_key": c.block_key, "block": c.block, "street": c.street_name,
                "town": c.town, "flat_type": c.flat_type, "lat": c.lat, "lon": c.lon,
                "rank_score": c.rank_score, "growth_score": c.growth_score,
                "median_price": c.median_price_24m, "drive_minutes": c.drive_minutes,
                "lease_years_at_exit": c.lease_years_at_exit,
                "badge": c.fit.badge if c.fit else None,
                "misses": [m.detail for m in c.fit.misses] if c.fit else [],
                "url": url_for("block_profile", block_key=c.block_key, flat_type=c.flat_type),
            }
            for c in candidates if c.lat is not None
        ])

    @app.route("/api/stations.json")
    @login_required
    def stations_json():
        db = get_db()
        return jsonify([
            dict(row) for row in db.execute(
                "SELECT name, line, status, expected_year, lat, lon FROM mrt_station"
                " WHERE lat IS NOT NULL"
            )
        ])

    # --- shortlist (F7) ---------------------------------------------------
    @app.route("/listings")
    @login_required
    def listings():
        db = get_db()
        rows = db.execute(
            "SELECT * FROM listing ORDER BY"
            " CASE status WHEN 'Offered' THEN 0 WHEN 'Viewed' THEN 1"
            "             WHEN 'Watching' THEN 2 ELSE 3 END,"
            " COALESCE(viewing_date, ''), id DESC"
        ).fetchall()
        valuer = Valuer(db)
        enriched = []
        for row in rows:
            estimate = valuer.estimate(
                row["block_key"] or "", row["flat_type"] or "",
                storey=row["storey"], floor_area_sqm=row["floor_area_sqm"],
            )
            enriched.append({
                "row": row,
                "estimate": estimate,
                "tag": estimate.tag(row["asking_price"]),
            })
        return render_template(
            "listings.html", listings=enriched, blocks=known_blocks(db),
        )

    @app.route("/listings/new", methods=["POST"])
    @login_required
    def create_listing():
        db = get_db()
        form = request.form
        block, street = split_block_key(form.get("block_key", ""))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cur = db.execute(
            "INSERT INTO listing (url, block_key, block, street_name, flat_type,"
            " asking_price, storey, floor_area_sqm, corner_unit, facing, status,"
            " viewing_date, notes, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                form.get("url") or None, form.get("block_key") or None, block, street,
                canon_flat_type(form.get("flat_type")) or None,
                to_float(form.get("asking_price")), to_int(form.get("storey")),
                to_float(form.get("floor_area_sqm")),
                1 if form.get("corner_unit") else 0, form.get("facing") or None,
                form.get("status") or "Watching", form.get("viewing_date") or None,
                form.get("notes") or None, now, now,
            ),
        )
        db.commit()
        return redirect(url_for("listing_detail", listing_id=cur.lastrowid))

    @app.route("/listings/<int:listing_id>", methods=["GET", "POST"])
    @login_required
    def listing_detail(listing_id: int):
        db = get_db()
        if request.method == "POST":
            form = request.form
            block, street = split_block_key(form.get("block_key", ""))
            db.execute(
                "UPDATE listing SET url=?, block_key=?, block=?, street_name=?,"
                " flat_type=?, asking_price=?, storey=?, floor_area_sqm=?,"
                " corner_unit=?, facing=?, status=?, viewing_date=?, notes=?,"
                " eip_checked=?, hardcourt_seen=?, updated_at=? WHERE id=?",
                (
                    form.get("url") or None, form.get("block_key") or None, block, street,
                    canon_flat_type(form.get("flat_type")) or None,
                    to_float(form.get("asking_price")), to_int(form.get("storey")),
                    to_float(form.get("floor_area_sqm")),
                    1 if form.get("corner_unit") else 0, form.get("facing") or None,
                    form.get("status") or "Watching", form.get("viewing_date") or None,
                    form.get("notes") or None,
                    1 if form.get("eip_checked") else 0,
                    1 if form.get("hardcourt_seen") else 0,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"), listing_id,
                ),
            )
            for key, _question in VIEWING_CHECKLIST:
                answer = form.get(f"check_{key}")
                note = form.get(f"note_{key}")
                if answer or note:
                    db.execute(
                        "INSERT INTO listing_check (listing_id, item, answer, note)"
                        " VALUES (?,?,?,?) ON CONFLICT(listing_id, item) DO UPDATE SET"
                        " answer=excluded.answer, note=excluded.note",
                        (listing_id, key, answer or None, note or None),
                    )
            db.commit()
            flash("Saved.")
            return redirect(url_for("listing_detail", listing_id=listing_id))

        row = db.execute("SELECT * FROM listing WHERE id = ?", (listing_id,)).fetchone()
        if not row:
            abort(404)
        valuer = Valuer(db)
        estimate = valuer.estimate(
            row["block_key"] or "", row["flat_type"] or "",
            storey=row["storey"], floor_area_sqm=row["floor_area_sqm"],
        )
        block = db.execute(
            "SELECT * FROM block WHERE block_key = ?", (row["block_key"],)
        ).fetchone()
        commute = db.execute(
            "SELECT MAX(drive_minutes) AS worst, MIN(status) AS status FROM commute"
            " WHERE block_key = ?", (row["block_key"],)
        ).fetchone()
        metric = db.execute(
            "SELECT * FROM block_metric WHERE block_key = ? AND flat_type = ?",
            (row["block_key"], row["flat_type"]),
        ).fetchone()
        lease_exit = None
        if metric and metric["median_lease_months"] is not None:
            cfg = load_config()
            lease_exit = (
                metric["median_lease_months"] / 12
                - max(0, int(cfg.household["purchase_year"])
                      - int((metric["last_txn_month"] or "2026-01")[:4]))
                - cfg.holding_years
            )
        fit = evaluate(
            block_key=row["block_key"] or "",
            town=block["town"] if block else None,
            flat_type=row["flat_type"] or "",
            price=row["asking_price"],
            price_kind="asking",
            storey=row["storey"],
            max_floor_lvl=block["max_floor_lvl"] if block else None,
            corner_unit=bool(row["corner_unit"]) if row["corner_unit"] is not None else None,
            drive_minutes=commute["worst"] if commute else None,
            commute_status=commute["status"] if commute else None,
            lease_years_at_exit=lease_exit,
        )
        answers = {
            r["item"]: r for r in db.execute(
                "SELECT * FROM listing_check WHERE listing_id = ?", (listing_id,)
            )
        }
        photos = db.execute(
            "SELECT * FROM listing_photo WHERE listing_id = ? ORDER BY id", (listing_id,)
        ).fetchall()
        return render_template(
            "listing.html", row=row, estimate=estimate,
            tag=estimate.tag(row["asking_price"]), fit=fit, block=block,
            checklist=VIEWING_CHECKLIST, answers=answers, photos=photos,
            blocks=known_blocks(db),
        )

    @app.route("/listings/<int:listing_id>/photo", methods=["POST"])
    @login_required
    def upload_photo(listing_id: int):
        db = get_db()
        if not db.execute("SELECT 1 FROM listing WHERE id = ?", (listing_id,)).fetchone():
            abort(404)
        file = request.files.get("photo")
        if not file or not file.filename:
            flash("No photo chosen.")
            return redirect(url_for("listing_detail", listing_id=listing_id))
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_PHOTO_EXT:
            flash(f"{suffix or 'that file'} is not a photo we can store.")
            return redirect(url_for("listing_detail", listing_id=listing_id))
        folder = PHOTO_DIR / str(listing_id)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        name = secure_filename(f"{stamp}-{file.filename}")
        file.save(folder / name)
        db.execute(
            "INSERT INTO listing_photo (listing_id, filename, caption, created_at)"
            " VALUES (?,?,?,?)",
            (listing_id, name, request.form.get("caption") or None,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        db.commit()
        return redirect(url_for("listing_detail", listing_id=listing_id))

    @app.route("/listings/<int:listing_id>/photo/<path:filename>")
    @login_required
    def listing_photo(listing_id: int, filename: str):
        return send_from_directory(PHOTO_DIR / str(listing_id), filename)

    @app.route("/listings/<int:listing_id>/delete", methods=["POST"])
    @login_required
    def delete_listing(listing_id: int):
        db = get_db()
        db.execute("DELETE FROM listing WHERE id = ?", (listing_id,))
        db.commit()
        flash("Listing removed.")
        return redirect(url_for("listings"))

    # --- data freshness (F8) ---------------------------------------------
    @app.route("/data")
    @login_required
    def data_page():
        db = get_db()
        return render_template(
            "data.html",
            syncs=freshness(db),
            log=db.execute(
                "SELECT * FROM sync_log ORDER BY finished_at DESC LIMIT 40"
            ).fetchall(),
            lease_curve=db.execute(
                "SELECT * FROM lease_factor WHERE flat_type = 'ALL' ORDER BY lease_band"
            ).fetchall(),
            storey_curve=db.execute(
                "SELECT * FROM storey_premium WHERE flat_type = 'ALL' ORDER BY storey_band"
            ).fetchall(),
            counts={
                table: db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                for table in ("resale_txn", "block", "rental_txn", "price_index",
                              "town_median", "mrt_station", "block_metric", "block_score")
            },
            commute_states=db.execute(
                "SELECT status, COUNT(*) AS n FROM commute GROUP BY status"
            ).fetchall(),
            geocoded=db.execute(
                "SELECT SUM(lat IS NOT NULL) AS done, COUNT(*) AS total FROM block"
            ).fetchone(),
            policy_checked=load_config().policy.get("last_checked"),
            datasets_checked=load_config().datasets.get("last_checked"),
        )

    # --- PWA --------------------------------------------------------------
    @app.route("/manifest.webmanifest")
    def manifest():
        return jsonify({
            "name": "Next Home Finder",
            "short_name": "Next Home",
            "start_url": "/",
            "display": "standalone",
            # SGDS gray-1100, the night theme's page background.
            "background_color": "#0e0e0e",
            "theme_color": "#0e0e0e",
            "icons": [
                {"src": url_for("static", filename="icon.svg"),
                 "sizes": "any", "type": "image/svg+xml"},
            ],
        })

    @app.route("/sw.js")
    def service_worker():
        response = send_from_directory(Path(__file__).parent / "static", "sw.js")
        response.headers["Content-Type"] = "application/javascript"
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    return app


# --- helpers --------------------------------------------------------------
def to_float(value) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def to_int(value) -> int | None:
    f = to_float(value)
    return int(round(f)) if f is not None else None


def split_block_key(block_key: str) -> tuple[str | None, str | None]:
    if "|" not in (block_key or ""):
        return (block_key or None, None)
    block, street = block_key.split("|", 1)
    return (block or None, street or None)


def read_controls(args) -> tuple[dict[str, float], dict]:
    """Weight sliders and filters from the query string."""
    cfg = load_config()
    weights = {}
    for key, default in cfg.weights.items():
        weights[key] = args.get(f"w_{key}", type=float, default=float(default))
    filters: dict = {}
    if args.get("max_price", type=float):
        filters["max_price"] = args.get("max_price", type=float)
    towns = [t for t in args.getlist("town") if t]
    if towns:
        filters["towns"] = towns
    types = [t for t in args.getlist("flat_type") if t]
    if types:
        filters["flat_types"] = types
    if args.get("min_rooms", type=int) is not None:
        filters["min_rooms"] = args.get("min_rooms", type=int)
    return weights, filters


def all_towns(conn) -> list[str]:
    return [r["town"] for r in conn.execute(
        "SELECT DISTINCT town FROM block WHERE town IS NOT NULL AND town <> ''"
        " ORDER BY town"
    )]


def all_flat_types(conn) -> list[str]:
    return [r["flat_type"] for r in conn.execute(
        "SELECT DISTINCT flat_type FROM block_metric ORDER BY flat_type"
    )]


def flat_types_in_block(conn, block_key: str) -> list[str]:
    return [r["flat_type"] for r in conn.execute(
        "SELECT DISTINCT flat_type FROM resale_txn WHERE block_key = ? ORDER BY flat_type",
        (block_key,),
    )]


def known_blocks(conn, limit: int = 4000) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT block_key, block, street_name, town FROM block"
        " WHERE EXISTS (SELECT 1 FROM resale_txn t WHERE t.block_key = block.block_key)"
        " ORDER BY town, street_name, block LIMIT ?", (limit,),
    )]


def recent_transactions(conn, block_key: str, months: int = 24) -> list[sqlite3.Row]:
    latest = conn.execute("SELECT MAX(month) AS m FROM resale_txn").fetchone()["m"]
    if not latest:
        return []
    from ..metrics import month_minus
    return conn.execute(
        "SELECT * FROM resale_txn WHERE block_key = ? AND month >= ?"
        " ORDER BY month DESC, flat_type",
        (block_key, month_minus(latest, months)),
    ).fetchall()


def medians_by_type(conn, block_key: str, months: int = 24) -> list[sqlite3.Row]:
    latest = conn.execute("SELECT MAX(month) AS m FROM resale_txn").fetchone()["m"]
    if not latest:
        return []
    from ..metrics import month_minus
    return conn.execute(
        """
        SELECT flat_type, COUNT(*) AS n, MIN(resale_price) AS low,
               MAX(resale_price) AS high, AVG(resale_price) AS mean,
               AVG(price_per_sqm) AS psm, AVG(floor_area_sqm) AS sqm
        FROM resale_txn WHERE block_key = ? AND month >= ?
        GROUP BY flat_type ORDER BY flat_type
        """,
        (block_key, month_minus(latest, months)),
    ).fetchall()


def medians_by_storey(conn, block_key: str, flat_type: str, months: int = 24):
    latest = conn.execute("SELECT MAX(month) AS m FROM resale_txn").fetchone()["m"]
    if not latest:
        return []
    from ..metrics import month_minus
    return conn.execute(
        """
        SELECT storey_range, COUNT(*) AS n, AVG(resale_price) AS mean,
               AVG(price_per_sqm) AS psm
        FROM resale_txn WHERE block_key = ? AND flat_type = ? AND month >= ?
        GROUP BY storey_range ORDER BY storey_low
        """,
        (block_key, flat_type, month_minus(latest, months)),
    ).fetchall()


def price_trend(conn, block_key: str, flat_type: str, town: str | None) -> list[dict]:
    """Yearly price per sqm for this block, its town and the national index."""
    block_rows = conn.execute(
        "SELECT SUBSTR(month, 1, 4) AS year, AVG(price_per_sqm) AS psm, COUNT(*) AS n"
        " FROM resale_txn WHERE block_key = ? AND flat_type = ?"
        " GROUP BY year ORDER BY year", (block_key, flat_type),
    ).fetchall()
    town_rows = {
        r["year"]: r["psm"] for r in conn.execute(
            "SELECT SUBSTR(month, 1, 4) AS year, AVG(price_per_sqm) AS psm"
            " FROM resale_txn WHERE town = ? AND flat_type = ?"
            " GROUP BY year", (town or "", flat_type),
        )
    }
    index_rows = {
        r["year"]: r["value"] for r in conn.execute(
            "SELECT SUBSTR(quarter, 1, 4) AS year, AVG(index_value) AS value"
            " FROM price_index GROUP BY year"
        )
    }
    return [
        {
            "year": r["year"], "n": r["n"], "block_psm": r["psm"],
            "town_psm": town_rows.get(r["year"]), "index": index_rows.get(r["year"]),
        }
        for r in block_rows
    ]


def nearby_stations(conn, block, radius_m: float = 2000) -> list[dict]:
    if block["lat"] is None:
        return []
    from ..geo import haversine_m
    out = []
    for row in conn.execute(
        "SELECT name, line, status, expected_year, lat, lon FROM mrt_station"
        " WHERE lat IS NOT NULL"
    ):
        distance = haversine_m(block["lat"], block["lon"], row["lat"], row["lon"])
        if distance <= radius_m:
            out.append(dict(row) | {"distance_m": round(distance)})
    return sorted(out, key=lambda s: s["distance_m"])[:6]


def freshness(conn) -> list[dict]:
    """Latest sync per dataset, with its age in days (F8)."""
    rows = conn.execute(
        """
        SELECT s.* FROM sync_log s
        JOIN (
            SELECT dataset_key, MAX(finished_at) AS latest
            FROM sync_log GROUP BY dataset_key
        ) latest ON latest.dataset_key = s.dataset_key AND latest.latest = s.finished_at
        ORDER BY s.dataset_key
        """
    ).fetchall()
    now = datetime.now(timezone.utc)
    out = []
    for row in rows:
        age_days = None
        try:
            age_days = (now - datetime.fromisoformat(row["finished_at"])).days
        except (TypeError, ValueError):
            pass
        out.append(dict(row) | {"age_days": age_days, "stale": (age_days or 0) > 45})
    return out
