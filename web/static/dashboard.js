/* Dashboard chart wiring.
 *
 * WHY THIS IS A FILE AND NOT INLINE
 * ---------------------------------
 * The CSP is `script-src 'self'`, so all chart code lives here and page data
 * arrives in non-executable `<script type="application/json">` blocks.
 *
 * WHO DECIDES WHAT
 * ----------------
 * Units, axis policies and ranges, precision, statistics, plausibility and
 * provenance are decided on the server (analysis/chart_semantics.py) where
 * they are tested. This file draws what it is given:
 *
 *   * a real time axis (milliseconds, UTC), shared by related panels;
 *   * straight segments between recorded points -- no spline, because a curve
 *     between two samples draws values nobody measured;
 *   * gaps wider than `gap_s` break the line instead of bridging it;
 *   * one unit per axis, with the unit in the axis title and the tooltip;
 *   * imported / simulated readings and values outside the documented range
 *     get distinct point SHAPES (not only colours) and say so in the tooltip;
 *   * calculated series are dashed;
 *   * an empty series says "no readings", never draws a flat zero.
 */
/* global Chart */
(function () {
  "use strict";

  // Categorical series colours, in this fixed order and never cycled past it.
  // The order is the colour-blind-safety mechanism: it is the validated
  // reference palette (worst adjacent-pair CVD dE 9.1, normal-vision dE 19.6
  // on a light surface). The old set opened with a navy and a mid blue that
  // could not be told apart, which is every two-series chart on the Battery
  // page. Identity is never colour-alone: every chart with >= 2 series has a
  // legend, and every series has a text row beneath the chart.
  var COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
                "#008300", "#6250d6", "#e34948"];
  var PALETTE = COLORS;
  var GRID = "#eceae6", AXIS = "#cfccc6", INK_2 = "#52514e", INK_3 = "#76746f";

  var PROV_TEXT = {
    reported: "reported by the vehicle",
    calculated: "calculated from other readings",
    estimated: "estimated",
    imported: "imported from a Car Scanner export (not read by this tool)",
    simulated: "simulated — describes no car",
    unknown: "provenance not recorded for this source",
  };
  var PROV_STYLE = { imported: "triangle", simulated: "rectRot" };

  function pad(n) { return String(n).padStart(2, "0"); }

  /** Full observation time, to the second, explicitly UTC. */
  function fmtFull(ms) {
    var d = new Date(ms);
    return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" +
      pad(d.getUTCDate()) + " " + pad(d.getUTCHours()) + ":" +
      pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds()) + " UTC";
  }

  function fmtTick(ms, span, narrow) {
    var d = new Date(ms);
    if (narrow) {
      if (span <= 24 * 3600e3) {
        return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
      }
      return pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate());
    }
    if (span <= 36 * 3600e3) {
      return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
    }
    if (span <= 8 * 86400e3) {
      return pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()) + " " +
        pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
    }
    return pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate());
  }

  /** Generic formatter, used only where no server precision is known. */
  function fmtNum(v) {
    if (!Number.isFinite(v)) return "—";
    var a = Math.abs(v);
    if (a === 0) return "0";
    if (a >= 1000) return String(Math.round(v));
    if (a >= 100) return v.toFixed(1);
    if (a >= 1) return v.toFixed(2);
    if (a >= 0.01) return v.toFixed(3);
    return v.toExponential(2);
  }

  function fmtFixed(v, decimals) {
    if (!Number.isFinite(v)) return "—";
    if (decimals == null) return fmtNum(v);
    return v.toFixed(decimals);
  }

  function readJSON(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent || "null");
    } catch (e) {
      return null;
    }
  }

  function gapMsFrom(seconds) {
    var s = Number(seconds);
    return Number.isFinite(s) && s > 0 ? s * 1000 : 0;
  }

  /** Points from an annotated series payload, keeping per-point provenance,
   *  plausibility flag and note. Non-finite values become null (a hole), never
   *  zero. */
  function pointsFromPayload(p) {
    var ts = p.ts || [], vals = p.value || [];
    var prov = p.provenance || [], oor = p.out_of_range || [];
    var notes = p.point_notes || [];
    var out = [];
    var n = Math.min(ts.length, vals.length);
    for (var i = 0; i < n; i++) {
      var x = Date.parse(ts[i]);
      if (!Number.isFinite(x)) continue;
      var y = vals[i];
      out.push({
        x: x,
        y: (typeof y === "number" && Number.isFinite(y)) ? y : null,
        p: prov[i] || null, o: !!oor[i], n: notes[i] || null,
      });
    }
    return out;
  }

  /** Insert a null point in the middle of a hole so Chart.js breaks the line.
   *  The inserted point carries no value and is never shown in a tooltip. */
  function breakGaps(points, gapMs) {
    if (!gapMs || gapMs <= 0) return points;
    var out = [];
    var lastX = null;
    for (var i = 0; i < points.length; i++) {
      var pt = points[i];
      if (lastX != null && pt.x - lastX > gapMs) {
        out.push({ x: lastX + (pt.x - lastX) / 2, y: null, gap: true });
      }
      out.push(pt);
      lastX = pt.x;
    }
    return out;
  }

  // ---- time axis: recording sessions or calendar -------------------------
  //
  // The car is read in short sessions days apart, so on a 30-day calendar axis
  // every chart was a few clusters of dots in empty space, and a session's
  // readings stacked into an unreadable vertical streak. In "sessions" mode the
  // idle time between sessions is collapsed into a marked break, so each
  // session gets real width; tooltips still give the exact UTC time. The
  // calendar axis is one click away, and the choice is remembered.

  var TIME_MODE_KEY = "odb.timeMode";
  var MIN_SESSION_MS = 10 * 60e3;

  function getTimeMode() {
    try {
      var v = window.localStorage.getItem(TIME_MODE_KEY);
      if (v === "calendar" || v === "sessions") return v;
    } catch (e) { /* storage unavailable: use the default */ }
    return "sessions";
  }

  function setTimeMode(mode) {
    try { window.localStorage.setItem(TIME_MODE_KEY, mode); } catch (e) { /* ok */ }
  }

  function fmtSpan(ms) {
    var m = Math.round(ms / 60e3);
    if (m < 60) return m + " min";
    var h = Math.round(m / 60);
    if (h < 48) return h + " h";
    return Math.round(h / 24) + " d";
  }

  /** Split timestamps into sessions wherever the gap exceeds `gapMs`, and lay
   *  them out on a compressed axis. Returns null when there is nothing to
   *  compress (one session, or no data). */
  function buildTimeline(xs, gapMs) {
    xs = xs.filter(Number.isFinite).sort(function (a, b) { return a - b; });
    if (!xs.length) return null;
    var idle = gapMs > 0 ? gapMs : 15 * 60e3;
    var sessions = [{ start: xs[0], end: xs[0] }];
    for (var i = 1; i < xs.length; i++) {
      var cur = sessions[sessions.length - 1];
      if (xs[i] - cur.end > idle) sessions.push({ start: xs[i], end: xs[i] });
      else cur.end = xs[i];
    }
    if (sessions.length < 2) return null;
    var total = 0;
    sessions.forEach(function (s) {
      s.dur = s.end - s.start;
      s.width = Math.max(s.dur, MIN_SESSION_MS);
      total += s.width;
    });
    // All breaks share ~15 % of the axis, however many there are: a fixed
    // width per break let 14 breaks take most of a narrow chart.
    var sep = Math.max(total * 0.15 / (sessions.length - 1), 60e3);
    var pos = 0;
    sessions.forEach(function (s, k) {
      s.offset = pos;
      pos += s.width + (k < sessions.length - 1 ? sep : 0);
    });
    function map(t) {
      for (var k = 0; k < sessions.length; k++) {
        var s = sessions[k];
        if (t <= s.end || k === sessions.length - 1) {
          if (t < s.start) {                      // inside the break before s
            var prev = sessions[k - 1];
            if (!prev) return s.offset;
            var f = (t - prev.end) / (s.start - prev.end);
            return prev.offset + prev.width + sep * Math.min(1, Math.max(0, f));
          }
          var within = s.dur > 0 ? (t - s.start) / s.dur * s.width : s.width / 2;
          return s.offset + Math.min(s.width, within);
        }
      }
      return pos;
    }
    return { sessions: sessions, sep: sep, total: pos, map: map };
  }

  /** Draws each collapsed break as a shaded band with how long the car was
   *  idle, so the compression is never invisible. */
  function breakPlugin(timeline) {
    return {
      id: "odbBreaks",
      beforeDatasetsDraw: function (chart) {
        if (!timeline) return;
        var area = chart.chartArea, x = chart.scales.x, ctx = chart.ctx;
        ctx.save();
        for (var k = 0; k < timeline.sessions.length - 1; k++) {
          var s = timeline.sessions[k], nxt = timeline.sessions[k + 1];
          var a = x.getPixelForValue(s.offset + s.width);
          var b = x.getPixelForValue(nxt.offset);
          if (!(b > a)) continue;
          // One soft band per break; no border lines -- with a dozen sessions
          // dashed edges turned the plot into stripes. A break too thin to
          // shade is still marked with a single faint line.
          if (b - a >= 3) {
            ctx.fillStyle = "rgba(118,116,111,0.12)";
            ctx.fillRect(a, area.top, b - a, area.bottom - area.top);
          } else {
            ctx.strokeStyle = "rgba(118,116,111,0.35)";
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo((a + b) / 2, area.top);
            ctx.lineTo((a + b) / 2, area.bottom); ctx.stroke();
          }
          if (b - a >= 30) {
            ctx.fillStyle = INK_3;
            ctx.font = "10px 'Segoe UI', Arial, sans-serif";
            ctx.textAlign = "center";
            ctx.fillText(fmtSpan(nxt.start - s.end), (a + b) / 2, area.top + 11);
          }
        }
        ctx.restore();
      },
    };
  }

  /** A two-button control that switches every chart on the page. */
  function timeModeControl(anchor, onChange) {
    if (!anchor || document.getElementById("time-mode")) return;
    var wrap = document.createElement("div");
    wrap.id = "time-mode";
    wrap.className = "time-mode";
    wrap.setAttribute("role", "group");
    wrap.setAttribute("aria-label", "Time axis");
    var label = document.createElement("span");
    label.textContent = "Time axis:";
    wrap.appendChild(label);
    var opts = [
      ["sessions", "Recording sessions",
       "Idle time between recording sessions is collapsed; breaks are marked"],
      ["calendar", "Calendar time", "Real elapsed time, including idle days"],
    ];
    var buttons = [];
    opts.forEach(function (o) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = o[1];
      btn.title = o[2];
      btn.setAttribute("aria-pressed", String(getTimeMode() === o[0]));
      btn.addEventListener("click", function () {
        setTimeMode(o[0]);
        buttons.forEach(function (b, i) {
          b.setAttribute("aria-pressed", String(opts[i][0] === o[0]));
        });
        onChange(o[0]);
      });
      buttons.push(btn);
      wrap.appendChild(btn);
    });
    anchor.parentNode.insertBefore(wrap, anchor);
  }

  function finiteValues(points) {
    var out = [];
    for (var i = 0; i < points.length; i++) {
      if (points[i].y != null) out.push(points[i].y);
    }
    return out;
  }

  function emptyFor(canvasId) {
    return document.getElementById(canvasId + "-empty");
  }

  function showEmpty(canvasId, message) {
    var canvas = document.getElementById(canvasId);
    var empty = emptyFor(canvasId);
    if (canvas) {
      canvas.style.display = "none";
      var wrap = canvas.closest(".chart-wrap");
      if (wrap) wrap.style.display = "none";
    }
    if (empty) {
      if (message) empty.textContent = message;
      empty.classList.add("show");
    }
  }

  function showCanvas(canvasId) {
    var canvas = document.getElementById(canvasId);
    var empty = emptyFor(canvasId);
    if (canvas) {
      canvas.style.display = "";
      var wrap = canvas.closest(".chart-wrap");
      if (wrap) wrap.style.display = "";
    }
    if (empty) empty.classList.remove("show");
  }

  function destroy(canvas) {
    if (window.Chart && Chart.getChart) {
      var existing = Chart.getChart(canvas);
      if (existing) existing.destroy();
    }
  }

  function markerPlugin(markers) {
    return {
      id: "odbMarkers",
      afterDatasetsDraw: function (chart) {
        if (!markers || !markers.length) return;
        var area = chart.chartArea;
        var x = chart.scales.x;
        var ctx = chart.ctx;
        ctx.save();
        ctx.strokeStyle = "rgba(192,57,43,0.38)";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 3]);
        for (var i = 0; i < markers.length; i++) {
          var px = x.getPixelForValue(markers[i].x);
          if (!Number.isFinite(px) || px < area.left || px > area.right) continue;
          ctx.beginPath();
          ctx.moveTo(px, area.top);
          ctx.lineTo(px, area.bottom);
          ctx.stroke();
        }
        ctx.restore();
      },
    };
  }

  /** The unit alone. The panel title already names the quantity, and the old
   *  "<series name> (<unit>)" was rotated and truncated on narrow charts
   *  ("voltage spread from individual cells (calcu"). */
  function axisTitle(meta, datasets) {
    if (!datasets.length) return undefined;
    var unit = meta && meta.unit_label ? meta.unit_label : "";
    return unit || "no unit";
  }

  function yScale(meta, datasets, position, fallbackZero) {
    var range = meta && meta.range;
    var tickDecimals = range ? range.tick_decimals : null;
    var title = axisTitle(meta, datasets);
    var scale = {
      position: position,
      beginAtZero: !!fallbackZero,
      grid: position === "right" ? { drawOnChartArea: false }
                                 : { color: GRID },
      border: { color: AXIS },
      title: title
        ? { display: true, text: title, font: { size: 11 }, color: INK_2 }
        : { display: false },
      ticks: {
        font: { size: 10 },
        color: INK_3,
        callback: function (value) {
          return tickDecimals == null ? fmtNum(Number(value))
                                      : Number(value).toFixed(tickDecimals);
        },
      },
    };
    if (range) {
      scale.min = range.min;
      scale.max = range.max;
      if (range.step) scale.ticks.stepSize = range.step;
    }
    return scale;
  }

  /**
   * Draw one line chart.
   *
   * spec = {
   *   gapMs, markers: [{x,label}], xRange: [msMin, msMax],
   *   axes: {left: axisMeta, right: axisMeta},
   *   series: [{key, label, unit_label, decimals, points, color, axis,
   *             verification, derived, hidden, plausible}]
   * }
   */
  function lineChart(canvasId, spec) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    if (!window.Chart) {
      showEmpty(canvasId, "Chart library failed to load.");
      return null;
    }
    destroy(canvas);

    var gapMs = spec.gapMs || 0;
    var timeline = spec.timeline || null;
    var datasets = [];
    var xMin = Infinity;
    var xMax = -Infinity;
    var anyRight = false;

    for (var i = 0; i < (spec.series || []).length; i++) {
      var s = spec.series[i];
      var points = breakGaps(s.points || [], gapMs);
      if (timeline) {
        // Gaps are found on real time first, then every point (and the null
        // that breaks the line) is placed on the compressed axis; `t` keeps
        // the real time for the tooltip.
        points = points.map(function (pt) {
          var q = {};
          for (var key in pt) q[key] = pt[key];
          q.t = pt.x;
          q.x = timeline.map(pt.x);
          return q;
        });
      }
      var vals = finiteValues(points);
      if (!vals.length) continue;
      for (var j = 0; j < points.length; j++) {
        if (points[j].y == null) continue;
        if (points[j].x < xMin) xMin = points[j].x;
        if (points[j].x > xMax) xMax = points[j].x;
      }
      var color = s.color || COLORS[Math.min(i, COLORS.length - 1)];
      var base = vals.length === 1 ? 4 : (vals.length <= 150 ? 2.5 : 0);
      datasets.push({
        key: s.key,
        label: s.label || "",
        unit: s.unit_label || "",
        decimals: s.decimals,
        verification: s.verification || "",
        derived: !!s.derived,
        plausible: s.plausible || null,
        data: points,
        hidden: !!s.hidden,
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
        borderDash: s.derived ? [5, 3] : [],
        pointStyle: function (ctx) {
          var r = ctx.raw;
          if (r && r.o) return "crossRot";
          return (r && PROV_STYLE[r.p]) || "circle";
        },
        pointRadius: (function (b) {
          return function (ctx) {
            var r = ctx.raw;
            if (!r || r.y == null) return 0;
            if (r.o) return 5;
            if (PROV_STYLE[r.p]) return 4;
            return b;
          };
        })(base),
        pointBorderWidth: function (ctx) {
          return ctx.raw && ctx.raw.o ? 2 : 0;
        },
        pointHitRadius: 9,
        tension: 0,
        stepped: false,
        spanGaps: false,
        fill: false,
        yAxisID: s.axis === "right" ? "y2" : "y",
      });
      if (s.axis === "right") anyRight = true;
    }

    if (!datasets.length || !Number.isFinite(xMin) || !Number.isFinite(xMax)) {
      showEmpty(canvasId);
      return null;
    }
    showCanvas(canvasId);

    if (timeline) {
      // Every panel on the page shares the same compressed axis.
      xMin = 0;
      xMax = timeline.total;
    } else if (spec.xRange && Number.isFinite(spec.xRange[0]) &&
        Number.isFinite(spec.xRange[1])) {
      xMin = Math.min(xMin, spec.xRange[0]);
      xMax = Math.max(xMax, spec.xRange[1]);
    }
    if (xMax === xMin) { xMin -= 60000; xMax += 60000; }
    var span = xMax - xMin;
    var narrow = (canvas.clientWidth || canvas.width || 0) < 480;
    var axes = spec.axes || {};

    var leftDs = datasets.filter(function (d) { return d.yAxisID === "y"; });
    var rightDs = datasets.filter(function (d) { return d.yAxisID === "y2"; });

    var startByOffset = {};
    if (timeline) {
      timeline.sessions.forEach(function (s) { startByOffset[s.offset] = s.start; });
    }
    var scales = {
      x: {
        type: "linear",
        min: xMin,
        max: xMax,
        grid: { color: GRID, drawTicks: false, display: !timeline },
        border: { color: AXIS },
        title: {
          display: true,
          text: timeline
            ? "Time (UTC) — each block is a recording session; shaded breaks hide the idle time"
            : "Time (UTC)",
          font: { size: 10 }, color: INK_3,
        },
        afterBuildTicks: timeline ? function (axis) {
          // One tick per session start: the dates are what anchor a reader.
          // On a narrow chart, one per calendar day -- several sessions on
          // the same day otherwise print the same date side by side.
          // Ticks are thinned by distance along the axis as well: Chart.js's
          // own label skipping does not apply to ticks set here, and two
          // sessions close together printed "10-210-24".
          var seen = {};
          var minGap = timeline.total * (narrow ? 0.16 : 0.08);
          var lastKept = -Infinity;
          axis.ticks = timeline.sessions.filter(function (s) {
            if (narrow) {
              var day = fmtTick(s.start, 9 * 86400e3, false);
              if (seen[day]) return false;
              seen[day] = true;
            }
            if (s.offset - lastKept < minGap) return false;
            lastKept = s.offset;
            return true;
          }).map(function (s) { return { value: s.offset }; });
        } : undefined,
        ticks: {
          maxTicksLimit: timeline ? undefined : (narrow ? 4 : 8),
          autoSkip: true,
          autoSkipPadding: 12,
          maxRotation: 0,
          font: { size: 10 },
          color: INK_3,
          callback: function (value) {
            if (timeline) {
              var start = startByOffset[value];
              return start == null ? "" : fmtTick(start, 9 * 86400e3, false) +
                (narrow ? "" : " " + fmtTick(start, 0, false));
            }
            return fmtTick(Number(value), span, narrow);
          },
        },
      },
      y: yScale(axes.left, leftDs, "left", spec.beginAtZero),
    };
    if (anyRight) scales.y2 = yScale(axes.right, rightDs, "right", false);

    return new Chart(canvas.getContext("2d"), {
      type: "line",
      data: { datasets: datasets },
      plugins: [
        breakPlugin(timeline),
        markerPlugin((spec.markers || []).map(function (m) {
          return timeline ? { x: timeline.map(m.x), label: m.label } : m;
        })),
      ],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        normalized: true,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: {
          legend: {
            display: datasets.length > 1,
            position: "top",
            align: "end",
            labels: { boxWidth: 12, boxHeight: 3, font: { size: 11 } },
          },
          tooltip: {
            displayColors: true,
            filter: function (item) { return item.raw && item.raw.y != null; },
            callbacks: {
              title: function (items) {
                if (!items.length) return "";
                var r = items[0].raw;
                return fmtFull(r.t != null ? r.t : r.x);
              },
              label: function (item) {
                var ds = item.dataset;
                var unit = ds.unit ? " " + ds.unit : "";
                return ds.label + ": " + fmtFixed(item.raw.y, ds.decimals) + unit;
              },
              afterLabel: function (item) {
                var ds = item.dataset, r = item.raw, lines = [];
                if (r.p) lines.push("source: " + (PROV_TEXT[r.p] || r.p));
                if (ds.derived) lines.push("calculated, not a vehicle reading");
                if (ds.verification) {
                  lines.push("decoder evidence: " + ds.verification.toUpperCase());
                }
                if (r.n) lines.push(r.n);
                if (r.o) {
                  var pr = ds.plausible;
                  lines.push("outside documented plausible range" +
                    (pr ? " [" + pr[0] + ", " + pr[1] + "]" : "") +
                    " — kept, not removed");
                }
                return lines;
              },
            },
          },
        },
        scales: scales,
      },
    });
  }

  function seriesSpec(payload, extra) {
    var spec = {
      key: payload.key,
      label: payload.name || payload.key,
      unit_label: payload.unit_label != null ? payload.unit_label : payload.unit,
      decimals: payload.axis ? payload.axis.decimals : null,
      verification: payload.verification,
      derived: payload.derived,
      plausible: payload.plausibility ? payload.plausibility.range : null,
      points: pointsFromPayload(payload),
    };
    for (var k in (extra || {})) spec[k] = extra[k];
    return spec;
  }

  function displayValue(value) {
    if (value == null) return "—";
    if (typeof value === "object") {
      try { return JSON.stringify(value); } catch (e) { return String(value); }
    }
    return String(value);
  }

  /** Text summary from SERVER statistics (computed on the raw rows, before
   *  any downsampling). Built with DOM nodes, not HTML strings. */
  function renderSummary(container, payload) {
    if (!container) return;
    container.innerHTML = "";
    var st = payload.stats;
    if (!st) return;
    var d = payload.axis ? payload.axis.decimals : null;
    var u = payload.unit_label ? " " + payload.unit_label : "";
    function add(label, value) {
      var span = document.createElement("span");
      span.appendChild(document.createTextNode(label + " "));
      var strong = document.createElement("strong");
      strong.textContent = value;
      span.appendChild(strong);
      container.appendChild(span);
    }
    add("readings", String(st.n));
    if (st.n) {
      add("min", fmtFixed(st.min, d) + u);
      add("max", fmtFixed(st.max, d) + u);
      add("mean", fmtFixed(st.mean, d) + u);
    }
    if (payload.verification) add("evidence", payload.verification.toUpperCase());
    var pc = payload.provenance_counts || {};
    for (var p in pc) {
      if (p !== "reported" && p !== "calculated") add(p, String(pc[p]));
    }
    if (payload.plausibility && payload.plausibility.outside) {
      add("outside documented range", String(payload.plausibility.outside));
    }
    if (payload.coverage && payload.coverage.gaps) {
      add("recording gaps", String(payload.coverage.gaps));
    }
    if (payload.axis && payload.axis.note) {
      var note = document.createElement("span");
      note.className = "axis-note-inline";
      note.textContent = "y-axis: " + payload.axis.note;
      container.appendChild(note);
    }
  }

  function renderStateStrip(container, payload) {
    if (!container) return;
    var ts = payload.ts || [];
    var vals = payload.value || [];
    var n = Math.min(ts.length, vals.length);
    container.innerHTML = "";
    if (!n) {
      container.style.display = "none";
      return;
    }
    container.style.display = "";

    var times = [];
    for (var k = 0; k < n; k++) {
      var t = Date.parse(ts[k]);
      times.push(Number.isFinite(t) ? t : null);
    }
    var first = times[0];
    var last = times[n - 1];
    if (first == null || last == null || last <= first) last = first + 1;

    var colors = {};
    var ci = 0;
    function colorFor(value) {
      var key = String(value);
      if (!(key in colors)) colors[key] = PALETTE[ci++ % PALETTE.length];
      return colors[key];
    }

    var row = document.createElement("div");
    row.className = "state-band-row";
    var segs = [];
    var total = 0;
    for (var i = 0; i < n; i++) {
      var start = times[i];
      if (start == null) continue;
      var end = (i + 1 < n && times[i + 1] != null) ? times[i + 1] : last;
      var w = Math.max(0.5, end - start);
      segs.push({ start: start, end: end, value: vals[i], w: w });
      total += w;
    }
    if (!total) total = 1;
    for (var s = 0; s < segs.length; s++) {
      var seg = segs[s];
      var band = document.createElement("span");
      band.className = "state-band";
      band.style.flex = "0 0 " + (seg.w / total * 100) + "%";
      band.style.background = colorFor(seg.value);
      band.textContent = displayValue(seg.value);
      band.title = fmtFull(seg.start) + " → " + fmtFull(seg.end) + ": " + seg.value;
      row.appendChild(band);
    }
    container.appendChild(row);

    var axis = document.createElement("div");
    axis.className = "state-axis";
    var a = document.createElement("span");
    var b = document.createElement("span");
    a.textContent = fmtFull(first);
    b.textContent = fmtFull(last);
    axis.appendChild(a);
    axis.appendChild(b);
    container.appendChild(axis);

    // Very short states are only a few pixels wide; list the transitions with
    // exact times so nothing is hidden by the proportional band.
    var list = document.createElement("ul");
    list.className = "state-list";
    var shown = Math.min(n, 30);
    for (var li = 0; li < shown; li++) {
      if (times[li] == null) continue;
      var item = document.createElement("li");
      item.textContent = fmtFull(times[li]) + " — " + displayValue(vals[li]);
      list.appendChild(item);
    }
    if (n > shown) {
      var more = document.createElement("li");
      more.textContent = "… " + (n - shown) + " more transitions";
      list.appendChild(more);
    }
    container.appendChild(list);
  }

  function singleSeriesChart(canvasId, payload, gapS, markers) {
    var gapMs = gapMsFrom(gapS);
    var timeline = getTimeMode() === "sessions"
      ? buildTimeline((payload.ts || []).map(function (t) { return Date.parse(t); }), gapMs)
      : null;
    return lineChart(canvasId, {
      gapMs: gapMs,
      timeline: timeline,
      markers: markers || [],
      axes: { left: payload.axis || null },
      series: [seriesSpec(payload)],
    });
  }

  function initMeasurements() {
    var data = readJSON("measurements-data");
    var sel = document.getElementById("meas");
    if (!data || !sel || !document.getElementById("chart")) return;

    var meta = document.getElementById("series-meta");
    var summary = document.getElementById("series-summary");
    var empty = document.getElementById("series-empty");
    var strip = document.getElementById("chart-state");
    var daysSel = document.getElementById("meas-days");
    var canvas = document.getElementById("chart");
    var wrap = canvas ? canvas.closest(".chart-wrap") : null;
    var current = null;
    // Changing the window and the key can start two requests. Without a token,
    // the slower one can resolve last and repaint the chart the user just left.
    var requestSeq = 0;

    function currentDays() {
      return daysSel ? Number(daysSel.value) : data.days;
    }

    function load(key) {
      var seq = ++requestSeq;
      if (current) { current.destroy(); current = null; }
      if (strip) { strip.innerHTML = ""; strip.style.display = "none"; }
      if (summary) summary.innerHTML = "";
      if (empty) { empty.classList.remove("show"); }
      if (canvas) canvas.style.display = "";
      if (wrap) wrap.style.display = "";

      var url = "/api/measurements/" + encodeURIComponent(key) +
        "?days=" + encodeURIComponent(currentDays()) +
        "&max_points=" + encodeURIComponent(data.max_points) +
        "&method=" + encodeURIComponent(data.method);
      fetch(url, { headers: { Accept: "application/json" } })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (payload) {
          if (seq !== requestSeq) return;
          if (meta) {
            var bits = [];
            if (payload.name) bits.push(payload.name);
            var unit = payload.unit_label != null ? payload.unit_label : payload.unit;
            if (unit) bits.push("(" + unit + ")");
            if (payload.raw_points != null) {
              bits.push(payload.raw_points + " raw → " +
                        payload.returned_points + " shown");
            }
            meta.textContent = bits.join(" · ");
          }

          var isText = payload.data_kind === "text" ||
                       payload.data_kind === "json" ||
                       payload.method === "transitions";
          if (isText) {
            if (canvas) canvas.style.display = "none";
            if (wrap) wrap.style.display = "none";
            renderStateStrip(strip, payload);
            return;
          }

          if (!payload.stats || !payload.stats.n) {
            if (canvas) canvas.style.display = "none";
            if (wrap) wrap.style.display = "none";
            if (empty) {
              empty.textContent = "No successful readings in the last " +
                currentDays() + " days.";
              empty.classList.add("show");
            }
            return;
          }
          renderSummary(summary, payload);
          current = singleSeriesChart("chart", payload, data.gap_s);
        })
        .catch(function (err) {
          if (seq !== requestSeq) return;
          if (empty) {
            empty.textContent = "Could not load this series: " + err.message;
            empty.classList.add("show");
          }
        });
    }

    timeModeControl(wrap, function () { if (sel.value) load(sel.value); });
    sel.addEventListener("change", function () { load(sel.value); });
    if (daysSel) {
      daysSel.addEventListener("change", function () { load(sel.value); });
    }
    var preferred = Array.prototype.find.call(
      sel.options, function (o) { return o.value === "pack_voltage"; });
    if (preferred) sel.value = preferred.value;
    if (sel.value) load(sel.value);
  }

  function initExplore() {
    var data = readJSON("explore-data");
    if (!data || !data.chart) return;
    var payload = data.chart;
    var canvas = document.getElementById("chart");
    var wrap = canvas ? canvas.closest(".chart-wrap") : null;
    var strip = document.getElementById("chart-state");
    var empty = document.getElementById("empty");

    if (!payload.ts || !payload.ts.length) {
      if (canvas) canvas.style.display = "none";
      if (wrap) wrap.style.display = "none";
      if (empty) empty.classList.add("show");
      return;
    }
    if (payload.method === "transitions") {
      if (canvas) canvas.style.display = "none";
      if (wrap) wrap.style.display = "none";
      renderStateStrip(strip, payload);
      return;
    }
    renderSummary(document.getElementById("explore-summary"), payload);
    var markers = (data.markers || []).map(function (m) {
      return { x: Date.parse(m.ts), label: m.label };
    }).filter(function (m) { return Number.isFinite(m.x); });
    var chart = singleSeriesChart("chart", payload, data.gap_s, markers);
    if (!chart) {
      if (canvas) canvas.style.display = "none";
      if (wrap) wrap.style.display = "none";
      if (empty) empty.classList.add("show");
      return;
    }
    timeModeControl(wrap, function () {
      singleSeriesChart("chart", payload, data.gap_s, markers);
    });
  }

  /** Server-built panels (overview, battery, range, charging session): one
   *  unit per axis, shared time axis, selectable series. */
  function initPanels() {
    var data = readJSON("panel-data");
    if (!data || !data.panels) return;
    var series = data.series || {};
    var visible = data.visible || {};
    var xr = data.x_range || null;
    var xRange = xr ? [Date.parse(xr[0]), Date.parse(xr[1])] : null;
    var gapMs = gapMsFrom(data.gap_s);

    // One timeline for the whole page, from every series it draws, so panels
    // stay aligned session for session.
    var allXs = [];
    Object.keys(series).forEach(function (k) {
      (series[k].ts || []).forEach(function (t) { allXs.push(Date.parse(t)); });
    });
    var pageTimeline = buildTimeline(allXs, gapMs);
    var charts = {};

    function render(mode) {
      var timeline = mode === "sessions" ? pageTimeline : null;
      data.panels.forEach(function (panel) {
        var canvasId = "panel-" + panel.id;
        if (panel.empty) { showEmpty(canvasId); return; }
        var specs = [];
        var idx = 0;
        function add(keys, side) {
          keys.forEach(function (k) {
            var p = series[k];
            if (!p) return;
            specs.push(seriesSpec(p, {
              axis: side, color: COLORS[Math.min(idx++, COLORS.length - 1)],
              hidden: charts[panel.id + ":" + k] != null
                ? !charts[panel.id + ":" + k] : visible[k] === false,
            }));
          });
        }
        add(panel.left || [], "left");
        add(panel.right || [], "right");
        var leftKey = (panel.left || [])[0];
        var rightKey = (panel.right || [])[0];
        var chart = lineChart(canvasId, {
          gapMs: gapMs,
          xRange: xRange,
          timeline: timeline,
          axes: {
            left: panel.left_axis || (leftKey && series[leftKey] ? series[leftKey].axis : null),
            right: panel.right_axis || (rightKey && series[rightKey] ? series[rightKey].axis : null),
          },
          series: specs,
        });
        charts[panel.id] = chart;
      });
    }

    render(getTimeMode());

    // Series checkboxes act on whichever chart is current for the panel, and
    // their state survives a time-axis switch.
    data.panels.forEach(function (panel) {
      var box = document.querySelector('fieldset[data-panel="' + panel.id + '"]');
      if (!box) return;
      box.querySelectorAll("input[data-key]").forEach(function (input) {
        input.addEventListener("change", function () {
          var key = input.getAttribute("data-key");
          charts[panel.id + ":" + key] = input.checked;
          var chart = charts[panel.id];
          if (!chart) return;
          for (var i = 0; i < chart.data.datasets.length; i++) {
            if (chart.data.datasets[i].key === key) {
              chart.setDatasetVisibility(i, input.checked);
            }
          }
          chart.update();
        });
      });
    });

    if (pageTimeline) {
      // The first card holding a time-series panel -- not just the first
      // card (the overview's position map comes before the charts).
      var firstCanvas = document.querySelector('.chart-card canvas[id^="panel-"]');
      var firstCard = firstCanvas ? firstCanvas.closest(".chart-card") : null;
      var anchor = firstCard && firstCard.parentElement &&
        firstCard.parentElement.classList.contains("chart-grid")
        ? firstCard.parentElement : firstCard;
      timeModeControl(anchor, render);
    }

    if (data.cell_bar) cellBar("cell-bar", data.cell_bar);
    if (data.scatter) rangeScatter("range-soc-scatter", data.scatter);
  }

  /** Latest sweep: one bar per physical cell, labelled with the catalogue's
   *  1-based cell number. */
  function cellBar(canvasId, info) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart) return;
    var cells = (info.latest && info.latest.cells) || [];
    if (!cells.length) { showEmpty(canvasId); return; }
    destroy(canvas);
    var range = info.axis && info.axis.range;
    var decimals = info.axis ? info.axis.decimals : 3;
    new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: cells.map(function (c) { return "#" + String(c.number).padStart(3, "0"); }),
        datasets: [{
          label: "Cell voltage",
          data: cells.map(function (c) { return c.voltage_v; }),
          backgroundColor: COLORS[0],
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: function (items) {
                var c = cells[items[0].dataIndex];
                return "Cell #" + String(c.number).padStart(3, "0") +
                  " (stored index " + c.index + ")";
              },
              label: function (item) {
                return fmtFixed(item.raw, decimals) + " V at " +
                  fmtFull(Date.parse(info.latest.ts));
              },
            },
          },
        },
        scales: {
          x: { title: { display: true, text: "Cell number" },
               ticks: { autoSkip: true, maxTicksLimit: 18, font: { size: 9 } } },
          y: {
            min: range ? range.min : undefined,
            max: range ? range.max : undefined,
            title: { display: true, text: "Cell voltage (V)" },
            ticks: {
              callback: function (v) {
                return range ? Number(v).toFixed(range.tick_decimals) : fmtNum(v);
              },
            },
          },
        },
      },
    });
  }

  function fitRange(vals, lo, hi) {
    var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
    var padv = (mx - mn) * 0.05 || Math.abs(mx) * 0.05 || 1;
    var a = mn - padv, b = mx + padv;
    if (lo != null) a = Math.max(a, lo);
    if (hi != null) b = Math.min(b, hi);
    return [a, b];
  }

  /** Range estimate vs SoC, paired by nearest timestamp. No fitted line: the
   *  relationship is not assumed to be linear (or anything else). */
  function rangeScatter(canvasId, info) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart) return;
    var pairs = info.pairs || [];
    if (!pairs.length) { showEmpty(canvasId); return; }
    destroy(canvas);
    var xs = pairs.map(function (p) { return p.x; });
    var ys = pairs.map(function (p) { return p.y; });
    var xr = fitRange(xs, 0, 100);
    var yr = fitRange(ys, 0, null);
    new Chart(canvas.getContext("2d"), {
      type: "scatter",
      data: { datasets: [{ label: "paired readings", data: pairs,
                           backgroundColor: COLORS[0], pointRadius: 3 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        parsing: { xAxisKey: "x", yAxisKey: "y" },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (item) {
                var p = item.raw;
                return [
                  info.x.label + ": " + fmtFixed(p.x, 1) + " " + info.x.unit_label +
                    " at " + fmtFull(Date.parse(p.ts_a)),
                  info.y.label + ": " + fmtFixed(p.y, 0) + " " + info.y.unit_label +
                    " at " + fmtFull(Date.parse(p.ts_b)),
                  "readings " + Math.abs(p.dt_s) + " s apart",
                ];
              },
            },
          },
        },
        scales: {
          x: { type: "linear", min: xr[0], max: xr[1],
               title: { display: true, text: info.x.label + " (" + info.x.unit_label + ")" } },
          y: { min: yr[0], max: yr[1],
               title: { display: true, text: info.y.label + " (" + info.y.unit_label + ")" } },
        },
      },
    });
  }

  function initProgress() {
    var data = readJSON("progress-data");
    var canvas = document.getElementById("ecuChart");
    if (!data || !canvas) return;
    if (!window.Chart) {
      showEmpty("ecuChart", "Chart library failed to load.");
      return;
    }
    var rows = (data.ecus || []).filter(function (r) {
      return (r.success || 0) + (r.failed || 0) > 0;
    });
    if (!rows.length) { showEmpty("ecuChart"); return; }
    showCanvas("ecuChart");
    destroy(canvas);
    new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: rows.map(function (r) { return r.ecu; }),
        datasets: [
          {
            label: "OK",
            data: rows.map(function (r) { return r.success || 0; }),
            backgroundColor: "#0ca30c",   // status: good
          },
          {
            label: "Failed",
            data: rows.map(function (r) { return r.failed || 0; }),
            backgroundColor: "#d03b3b",   // status: critical
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { position: "top", align: "end" } },
        scales: {
          x: { stacked: true, beginAtZero: true, ticks: { precision: 0 },
               title: { display: true, text: "Reads (count)" } },
          y: { stacked: true, ticks: { font: { size: 10 } } },
        },
      },
    });
  }

  /** Overview car-position map: OpenStreetMap tiles via the vendored Leaflet,
   *  the newest fix as the marker and earlier fixes in the window as a trail.
   *  Nothing is drawn between fixes beyond a straight line: the route between
   *  two sessions is unknown. */
  function initMap() {
    var data = readJSON("position-data");
    var el = document.getElementById("position-map");
    if (!data || !el) return;
    if (!window.L) {
      el.textContent = data.latest
        ? "Map library failed to load; the coordinates above are the position."
        : "Map library failed to load.";
      el.classList.add("chart-empty", "show");
      return;
    }
    var map = L.map(el, { scrollWheelZoom: false, attributionControl: true });
    if (data.tiles === "osm") osmLayer().addTo(map);
    if (!data.latest) {
      // No fix yet: the configured default view and no marker -- the card's
      // overlay says why. A guessed dot would be worse than an empty map.
      var v = data.default_view || [50, 10, 5];
      map.setView([v[0], v[1]], v[2]);
      return;
    }
    var latest = [data.latest.lat, data.latest.lon];
    var trail = (data.trail || []).map(function (f) { return [f.lat, f.lon]; });
    if (trail.length > 1) {
      L.polyline(trail, { color: INK_3, weight: 2, opacity: 0.55,
                          dashArray: "4 4" }).addTo(map);
      data.trail.slice(0, -1).forEach(function (f) {
        L.circleMarker([f.lat, f.lon], {
          radius: 4, color: "#ffffff", weight: 1, fillColor: INK_3, fillOpacity: 0.8,
        }).bindPopup(fmtFull(Date.parse(f.ts))).addTo(map);
      });
    }
    L.circleMarker(latest, {
      radius: 9, color: "#ffffff", weight: 3, fillColor: COLORS[0], fillOpacity: 1,
    }).bindPopup("Last recorded position<br>" + fmtFull(Date.parse(data.latest.ts)))
      .addTo(map);
    if (trail.length > 1) {
      map.fitBounds(L.latLngBounds(trail).extend(latest), { padding: [24, 24], maxZoom: 16 });
    } else {
      map.setView(latest, 15);
    }
  }

  function osmLayer() {
    return L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      // The page sends no Referer (it is policy: the URL can carry the
      // token). OSM's tile policy wants one from web pages; the origin
      // alone carries no path and no token.
      referrerPolicy: "origin",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" ' +
        'target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors',
    });
  }

  /** A popup body built from text nodes: charger names and addresses come
   *  from Open Charge Map and geocoder labels from OpenRouteService, so they
   *  never go through innerHTML. */
  function textPopup(lines) {
    var div = document.createElement("div");
    lines.forEach(function (t, i) {
      if (i) div.appendChild(document.createElement("br"));
      div.appendChild(document.createTextNode(String(t)));
    });
    return div;
  }

  /** Route planner page: the route line with start, destination and numbered
   *  charging stops on the map, and state of charge against distance. */
  function initRoute() {
    var data = readJSON("route-data");
    if (!data) return;
    var el = document.getElementById("route-map");
    if (el && window.L && data.line && data.line.length) {
      var map = L.map(el, { scrollWheelZoom: false, zoomSnap: 0.25 });
      if (data.tiles === "osm") osmLayer().addTo(map);
      var line = L.polyline(data.line, { color: COLORS[0], weight: 4, opacity: 0.85 }).addTo(map);
      // Heavy traffic over the route: orange, very heavy red.
      (data.traffic || []).forEach(function (t) {
        L.polyline(t.line, { color: t.level === "severe" ? "#b71c1c" : "#e65100",
                             weight: 6, opacity: 0.9 })
          .bindPopup(textPopup([t.level === "severe" ? "Very heavy traffic" : "Heavy traffic"]))
          .addTo(map);
      });
      [[data.origin, "Start"], [data.destination, "Destination"]].forEach(function (p) {
        L.circleMarker([p[0].lat, p[0].lon], {
          radius: 7, color: "#ffffff", weight: 2, fillColor: INK_2, fillOpacity: 1,
        }).bindPopup(textPopup([p[1], p[0].label])).addTo(map);
      });
      (data.stops || []).forEach(function (s, k) {
        L.marker([s.lat, s.lon], {
          icon: L.divIcon({ className: "route-stop-icon", html: String(k + 1),
                            iconSize: [22, 22] }),
          title: "Stop " + (k + 1) + ": " + s.name,
        }).bindPopup(textPopup([(k + 1) + ". " + s.name,
          Math.round(s.power_kw) + " kW · " + s.arrive_pct + " % → " + s.depart_pct +
          " % · " + s.charge_min + " min"])).addTo(map);
      });
      map.fitBounds(line.getBounds(), { padding: [24, 24] });
    } else if (el) {
      el.textContent = "Map library failed to load.";
      el.classList.add("chart-empty", "show");
    }

    var canvas = document.getElementById("route-soc");
    if (!canvas || !window.Chart || !data.profile || !data.profile.length) return;
    var pts = data.profile.map(function (p) { return { x: p[0], y: p[1] }; });
    var endKm = pts[pts.length - 1].x;
    new Chart(canvas, {
      type: "line",
      data: { datasets: [
        { label: "State of charge", data: pts, borderColor: COLORS[0],
          backgroundColor: COLORS[0], borderWidth: 2, pointRadius: 0, tension: 0 },
        { label: "Reserve (" + data.reserve + " %)",
          data: [{ x: 0, y: data.reserve }, { x: endKm, y: data.reserve }],
          borderColor: INK_3, borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 },
      ] },
      options: {
        maintainAspectRatio: false, animation: false, parsing: false,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: {
          legend: { position: "top", align: "end" },
          tooltip: { callbacks: {
            title: function (items) { return fmtFixed(items[0].parsed.x, 0) + " km"; },
            label: function (item) {
              return item.dataset.label + ": " + fmtFixed(item.parsed.y, 1) + " %";
            },
          } },
        },
        scales: {
          x: { type: "linear", min: 0, max: endKm, grid: { color: GRID },
               ticks: { callback: function (v) { return Math.round(v); } },
               title: { display: true, text: "Distance (km)" } },
          y: { min: 0, max: 100, grid: { color: GRID },
               title: { display: true, text: "State of charge (%)" } },
        },
      },
    });
  }

  function boot() {
    if (window.Chart) {
      Chart.defaults.font.family = "'Segoe UI', Arial, sans-serif";
      Chart.defaults.font.size = 11;
      Chart.defaults.color = "#556";
    }
    initMeasurements();
    initExplore();
    initPanels();
    initProgress();
    initMap();
    initRoute();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
