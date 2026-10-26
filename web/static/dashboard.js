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

  var COLORS = ["#0b2340", "#2471a3", "#1e8449", "#e67e22", "#8e44ad",
                "#c0392b", "#7f8c8d", "#16a085"];
  var PALETTE = ["#0b2340", "#2471a3", "#1e8449", "#e67e22", "#8e44ad",
                 "#c0392b", "#7f8c8d", "#16a085", "#2c3e50", "#d35400"];

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

  function axisTitle(meta, datasets) {
    if (!datasets.length) return undefined;
    var unit = meta && meta.unit_label ? meta.unit_label : "";
    if (datasets.length === 1) {
      return datasets[0].label + (unit ? " (" + unit + ")" : "");
    }
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
                                 : { color: "#eef2f5" },
      border: { color: "#cfd8e0" },
      title: title
        ? { display: true, text: title, font: { size: 11 }, color: "#556" }
        : { display: false },
      ticks: {
        font: { size: 10 },
        color: "#667",
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
    var datasets = [];
    var xMin = Infinity;
    var xMax = -Infinity;
    var anyRight = false;

    for (var i = 0; i < (spec.series || []).length; i++) {
      var s = spec.series[i];
      var points = breakGaps(s.points || [], gapMs);
      var vals = finiteValues(points);
      if (!vals.length) continue;
      for (var j = 0; j < points.length; j++) {
        if (points[j].y == null) continue;
        if (points[j].x < xMin) xMin = points[j].x;
        if (points[j].x > xMax) xMax = points[j].x;
      }
      var color = s.color || COLORS[i % COLORS.length];
      var base = vals.length === 1 ? 3.5 : (vals.length <= 150 ? 1.8 : 0);
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
        borderWidth: 1.7,
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

    if (spec.xRange && Number.isFinite(spec.xRange[0]) &&
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

    var scales = {
      x: {
        type: "linear",
        min: xMin,
        max: xMax,
        grid: { color: "#eef2f5", drawTicks: false },
        border: { color: "#cfd8e0" },
        title: { display: true, text: "Time (UTC)", font: { size: 10 },
                 color: "#778" },
        ticks: {
          maxTicksLimit: narrow ? 4 : 8,
          font: { size: 10 },
          color: "#667",
          callback: function (value) {
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
      plugins: [markerPlugin(spec.markers || [])],
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
                return items.length ? fmtFull(items[0].raw.x) : "";
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
    return lineChart(canvasId, {
      gapMs: gapMsFrom(gapS),
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
    }
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
            axis: side, color: COLORS[idx++ % COLORS.length],
            hidden: visible[k] === false,
          }));
        });
      }
      add(panel.left || [], "left");
      add(panel.right || [], "right");
      var leftKey = (panel.left || [])[0];
      var rightKey = (panel.right || [])[0];
      var chart = lineChart(canvasId, {
        gapMs: gapMsFrom(data.gap_s),
        xRange: xRange,
        axes: {
          left: panel.left_axis || (leftKey && series[leftKey] ? series[leftKey].axis : null),
          right: panel.right_axis || (rightKey && series[rightKey] ? series[rightKey].axis : null),
        },
        series: specs,
      });
      var box = document.querySelector('fieldset[data-panel="' + panel.id + '"]');
      if (!chart || !box) return;
      box.querySelectorAll("input[data-key]").forEach(function (input) {
        input.addEventListener("change", function () {
          var key = input.getAttribute("data-key");
          for (var i = 0; i < chart.data.datasets.length; i++) {
            if (chart.data.datasets[i].key === key) {
              chart.setDatasetVisibility(i, input.checked);
            }
          }
          chart.update();
        });
      });
    });

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
          backgroundColor: "#2471a3",
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
                           backgroundColor: "#2471a3", pointRadius: 3 }] },
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
            backgroundColor: "#1e8449",
          },
          {
            label: "Failed",
            data: rows.map(function (r) { return r.failed || 0; }),
            backgroundColor: "#c0392b",
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
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
