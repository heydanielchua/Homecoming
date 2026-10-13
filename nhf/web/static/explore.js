/* Ranking in the browser, for the published copy.
 *
 * The served app re-ranks on the server for every slider move. A static host
 * cannot, but it does not need to: four of the five sub-scores are measured on
 * fixed scales, so they are published already scored and weighting them is a
 * weighted mean. The fifth, price, is scored here from a budget the viewer
 * types in, because its scale is defined against that budget and publishing
 * the percentile would publish the budget.
 *
 * The arithmetic mirrors ranking.py deliberately, including the part that
 * matters most: weights are re-based on the sub-scores actually available, so
 * a missing drive time lowers confidence rather than quietly lowering a score.
 */
(function () {
  "use strict";

  var STORE = "nhf.explore.v1";
  var LABELS = {
    price: "Price", lease: "Lease", growth: "Growth",
    commute: "Commute", amenities: "Amenities"
  };

  var state = {rows: [], scales: null, meta: null, weights: {}, budget: null,
               maxPrice: null, towns: [], types: [], hideFlagged: false};

  // --- storage, which may be unavailable or throw in a private window ----
  function load() {
    try {
      var raw = window.localStorage.getItem(STORE);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }
  function save() {
    try {
      window.localStorage.setItem(STORE, JSON.stringify({
        weights: state.weights, budget: state.budget, maxPrice: state.maxPrice,
        towns: state.towns, types: state.types, hideFlagged: state.hideFlagged
      }));
    } catch (e) { /* nothing here is worth failing a render over */ }
  }

  // --- the scorer, mirroring nhf/stats.py and nhf/ranking.py ------------
  function clamp(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }

  function linearScore(value, best, worst) {
    if (value === null || value === undefined || best === worst) return null;
    return clamp((value - worst) / (best - worst));
  }

  function subScores(row) {
    var s = state.scales.rank_scales;
    var priceScore = null;
    var priceNote = "no budget set in this browser";
    if (state.budget) {
      var pb = s.price_budget_multiple;
      priceScore = linearScore(row.median_price,
                               state.budget * pb.best, state.budget * pb.worst);
      if (priceScore === null) priceNote = "no recent sales in this block";
      else if (priceScore === 1) {
        priceNote = "comfortably within budget (at or under " +
          money(state.budget * pb.best) + ")";
      } else priceNote = null;
    }
    var amenityNote = null;
    if (row.nearest_mrt_m !== null && row.nearest_mrt_m !== undefined &&
        row.nearest_mrt_m > state.scales.amenities_mrt_walk_m) {
      amenityNote = "nearest MRT is " + num(row.nearest_mrt_m, 0) +
        " m away, beyond a " + num(state.scales.amenities_mrt_walk_m, 0) + " m walk";
    }
    return [
      {key: "price", value: row.median_price, score: priceScore, note: priceNote,
       unit: state.budget
         ? "median recent sale, scored from " + money(state.budget * s.price_budget_multiple.best) +
           " down to " + money(state.budget * s.price_budget_multiple.worst)
         : "median recent sale, scored against a budget you set"},
      {key: "lease", value: row.lease_years_at_exit, unit:
         "years of lease left at exit, scored to " + s.lease_years_at_exit.best,
       score: linearScore(row.lease_years_at_exit,
                          s.lease_years_at_exit.best, s.lease_years_at_exit.worst),
       note: row.lease_years_at_exit === null ? "no lease information for this block" : null},
      {key: "growth", value: row.growth_score,
       unit: "growth score, 0-100, relative to the other blocks published",
       score: row.growth_score === null || row.growth_score === undefined
         ? null : row.growth_score / 100,
       note: row.growth_score === null || row.growth_score === undefined
         ? "growth score not computed" : null},
      {key: "commute", value: row.drive_minutes, unit:
         "minutes to the farther office, scored from " + s.drive_minutes.best +
         " to " + s.drive_minutes.worst,
       score: linearScore(row.drive_minutes, s.drive_minutes.best, s.drive_minutes.worst),
       note: row.drive_minutes === null || row.drive_minutes === undefined
         ? "not routed: drive times are not published" : null},
      {key: "amenities", value: row.nearest_mrt_m, unit:
         "metres to the nearest MRT station, scored from " + s.mrt_walk_m.best +
         " to " + s.mrt_walk_m.worst,
       score: linearScore(row.nearest_mrt_m, s.mrt_walk_m.best, s.mrt_walk_m.worst),
       note: row.nearest_mrt_m === null || row.nearest_mrt_m === undefined
         ? "no MRT station list loaded" : amenityNote}
    ];
  }

  function rankScore(subs) {
    var used = 0, total = 0;
    subs.forEach(function (sub) {
      var w = Number(state.weights[sub.key] || 0);
      if (sub.score === null || sub.score === undefined) return;
      used += w;
      total += w * sub.score;
    });
    return used ? Math.round(1000 * total / used) / 10 : null;
  }

  // --- formatting, mirroring the Jinja filters ---------------------------
  function money(v) {
    if (v === null || v === undefined) return "—";
    return "$" + Math.round(v).toLocaleString("en-US");
  }
  function num(v, places) {
    if (v === null || v === undefined) return "—";
    return Number(v).toLocaleString("en-US",
      {minimumFractionDigits: places, maximumFractionDigits: places});
  }
  function title(s) {
    if (!s) return "";
    return String(s).toLowerCase().replace(/\b[a-z]/g, function (c) {
      return c.toUpperCase();
    });
  }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function chipClass(v) {
    return v >= 70 ? "c-hi" : v >= 50 ? "c-mid" : "c-lo";
  }

  // --- rendering ---------------------------------------------------------
  function card(row) {
    var subs = subScores(row);
    var score = rankScore(subs);
    var head = esc(row.block) + " " + esc(title(row.street));
    var inner =
      '<div class="headline"><div><h2>' + head + "</h2>" +
      '<p class="sub">' + esc(title(row.flat_type)) + " · " + esc(title(row.town)) +
      (row.median_sqm ? " · " + num(row.median_sqm, 0) + " sqm" : "") +
      "</p></div>" +
      '<span class="chip ' + (score === null ? "c-lo" : chipClass(score)) + '">' +
      (score === null ? "—" : score) + "<small>rank</small></span></div>" +
      '<dl class="facts">' +
      fact("Median sale", money(row.median_price)) +
      fact("Growth", row.growth_score === null || row.growth_score === undefined
        ? "—" : row.growth_score) +
      fact("Lease at exit", num(row.lease_years_at_exit, 0) + " yrs") +
      fact("Drive", num(row.drive_minutes, 0) + " min") +
      fact("MRT", num(row.nearest_mrt_m, 0) + " m") +
      fact("Sales 24m", row.n_txn_24m) +
      "</dl>" +
      '<p class="badge' + (row.flags && row.flags.length ? " bad" : "") + '">' +
      esc(row.badge) + "</p>" +
      (row.flags && row.flags.length
        ? '<ul class="flags">' + row.flags.map(function (f) {
            return "<li>" + esc(f.label) + ": " + esc(f.detail) + "</li>";
          }).join("") + "</ul>"
        : "");

    var link = row.url
      ? '<a class="rowlink" href="' + esc(row.url) + '">' + inner + "</a>"
      : '<div class="rowlink">' + inner +
        '<p class="muted">No block page published for this one.</p></div>';

    var rows = subs.map(function (sub) {
      var pct = sub.score === null || sub.score === undefined
        ? "—" : Math.round(sub.score * 100);
      var tr = '<tr class="' + (sub.score === null ? "unk" : "") + '"><td>' +
        LABELS[sub.key] + "<small>" + esc(sub.unit) + "</small></td><td>" +
        (sub.value === null || sub.value === undefined ? "—" : num(sub.value, 0)) +
        "</td><td>" + pct + "</td><td>" +
        Math.round(Number(state.weights[sub.key] || 0)) + "</td></tr>";
      if (sub.note) {
        tr += '<tr class="note"><td colspan="4">' + esc(sub.note) + "</td></tr>";
      }
      return tr;
    }).join("");

    return '<article class="card candidate">' + link +
      "<details><summary>How this score was built</summary>" +
      '<table class="sub-scores"><tr><th>Part</th><th>Value</th><th>Score</th>' +
      "<th>Weight</th></tr>" + rows + "</table></details></article>";
  }

  function fact(label, value) {
    return "<div><dt>" + label + "</dt><dd>" + value + "</dd></div>";
  }

  function visible() {
    return state.rows.filter(function (row) {
      if (state.maxPrice && row.median_price && row.median_price > state.maxPrice) return false;
      if (state.types.length && state.types.indexOf(row.flat_type) < 0) return false;
      if (state.towns.length && state.towns.indexOf(row.town) < 0) return false;
      if (state.hideFlagged && row.flags && row.flags.length) return false;
      return true;
    });
  }

  function render() {
    var rows = visible().map(function (row) {
      var subs = subScores(row);
      return {row: row, score: rankScore(subs)};
    });
    // Ties are common because the price scale plateaus, so break them on
    // price: of two equally good blocks, the cheaper one wins.
    rows.sort(function (a, b) {
      if ((a.score === null) !== (b.score === null)) return a.score === null ? 1 : -1;
      if (b.score !== a.score) return (b.score || 0) - (a.score || 0);
      var ap = a.row.median_price === null ? Infinity : a.row.median_price;
      var bp = b.row.median_price === null ? Infinity : b.row.median_price;
      return ap - bp;
    });

    var shown = rows.slice(0, 200);
    document.getElementById("summary").textContent =
      "Showing " + shown.length + " of " + rows.length +
      ". Scores are weighted sub-scores on fixed scales" +
      (state.budget ? "" : "; price is unscored until you set a budget") + ".";
    document.getElementById("count").textContent = rows.length + " candidates";
    document.getElementById("list").innerHTML =
      shown.map(function (r) { return card(r.row); }).join("") ||
      '<section class="card"><h2>Nothing matches</h2><p>Widen the filters.</p></section>';
    save();
  }

  // --- controls ----------------------------------------------------------
  function buildSliders() {
    var host = document.getElementById("sliders");
    host.innerHTML = Object.keys(LABELS).map(function (key) {
      var v = Math.round(Number(state.weights[key] || 0));
      return '<label class="slider"><span>' + LABELS[key] + "<b>" + v +
        '</b></span><input type="range" data-w="' + key +
        '" min="0" max="50" step="5" value="' + v + '"></label>';
    }).join("");
    host.querySelectorAll("input[data-w]").forEach(function (input) {
      input.addEventListener("input", function () {
        state.weights[input.getAttribute("data-w")] = Number(input.value);
        input.previousElementSibling.querySelector("b").textContent = input.value;
        render();
      });
    });
  }

  function fillSelect(id, values, selected) {
    var el = document.getElementById(id);
    el.innerHTML = values.map(function (v) {
      return '<option value="' + esc(v) + '"' +
        (selected.indexOf(v) >= 0 ? " selected" : "") + ">" + esc(title(v)) + "</option>";
    }).join("");
    el.addEventListener("change", function () {
      var picked = [];
      Array.prototype.forEach.call(el.selectedOptions, function (o) { picked.push(o.value); });
      if (id === "town") state.towns = picked; else state.types = picked;
      render();
    });
  }

  function wire() {
    var budget = document.getElementById("budget");
    if (state.budget) budget.value = state.budget;
    budget.addEventListener("change", function () {
      state.budget = Number(budget.value) || null;
      render();
    });

    var maxPrice = document.getElementById("max_price");
    if (state.maxPrice) maxPrice.value = state.maxPrice;
    maxPrice.addEventListener("change", function () {
      state.maxPrice = Number(maxPrice.value) || null;
      render();
    });

    var hide = document.getElementById("hide_flagged");
    hide.checked = !!state.hideFlagged;
    hide.addEventListener("change", function () {
      state.hideFlagged = hide.checked;
      render();
    });

    document.getElementById("reset").addEventListener("click", function () {
      try { window.localStorage.removeItem(STORE); } catch (e) {}
      window.location.reload();
    });
  }

  function uniq(values) {
    var seen = {}, out = [];
    values.forEach(function (v) {
      if (v && !seen[v]) { seen[v] = 1; out.push(v); }
    });
    return out.sort();
  }

  function fail(message) {
    document.getElementById("summary").textContent = message;
  }

  Promise.all([
    fetch("data/candidates.json").then(function (r) { return r.json(); }),
    fetch("data/scales.json").then(function (r) { return r.json(); }),
    fetch("data/meta.json").then(function (r) { return r.json(); }).catch(function () { return null; })
  ]).then(function (results) {
    state.rows = results[0];
    state.scales = results[1];
    state.meta = results[2];

    var saved = load() || {};
    state.weights = saved.weights || state.scales.weights;
    state.budget = saved.budget || null;
    state.maxPrice = saved.maxPrice || null;
    state.towns = saved.towns || [];
    state.types = saved.types || [];
    state.hideFlagged = !!saved.hideFlagged;

    buildSliders();
    fillSelect("flat_type", uniq(state.rows.map(function (r) { return r.flat_type; })),
               state.types);
    fillSelect("town", uniq(state.rows.map(function (r) { return r.town; })), state.towns);
    wire();
    render();

    if (state.meta) {
      document.getElementById("meta-note").textContent =
        state.meta.note + " Built " + state.meta.built_at + ". Left out: " +
        state.meta.withheld.join("; ") + ".";
    }
  }).catch(function (err) {
    fail("Could not load the published data: " + err);
  });
})();
