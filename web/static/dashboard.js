/* Dashboard chart wiring.
 *
 * WHY THIS IS A FILE AND NOT INLINE
 * ---------------------------------
 * The CSP is `script-src 'self'`. It used to also allow the Chart.js CDN but
 * not inline scripts, so every page's inline `new Chart(...)` block was refused
 * by the browser: the library loaded and nothing ever drew. All chart code now
 * lives here, served from the dashboard's own origin, and page data arrives in
 * non-executable `<script type="application/json">` blocks.
 *
 * WHAT "BETTER" MEANS HERE
 * ------------------------
 *   * a real time axis (linear milliseconds) instead of equally spaced
 *     category labels, so a three-day outage looks like three days;
 *   * gaps are gaps: samples further apart than `gap_s` are not joined by a
 *     straight line that claims data nobody measured;
 *   * units are on the axis and in the tooltip;
 *   * empty series say "no readings", not an empty pair of axes;
 *   * text signals (gear, charge mode) render as a state timeline rather than
 *     being forced onto a numeric axis.
 */
/* global Chart */
(function () {
  "use strict";

  var COLORS = ["#0b2340", "#2471a3", "#1e8449", "#e67e22", "#8e44ad",
                "#c0392b", "#7f8c8d", "#16a085"];
  var PALETTE = ["#0b2340", "#2471a3", "#1e8449", "#e67e22", "#8e44ad",
                 "#c0392b", "#7f8c8d", "#16a085", "#2c3e50", "#d35400"];

  function pad(n) { return String(n).padStart(2, "0"); }

  function fmtFull(ms) {
    var d = new Date(ms);
    return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" +
      pad(d.getUTCDate()) + " " + pad(d.getUTCHours()) + ":" +
      pad(d.getUTCMinutes()) + " UTC";
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

  function zipPoints(ts, values) {
    var out = [];
    var n = Math.min((ts || []).length, (values || []).length);
    for (var i = 0; i < n; i++) {
      var x = Date.parse(ts[i]);
      if (!Number.isFinite(x)) continue;
      var y = values[i];
      out.push({ x: x, y: (typeof y === "number" && Number.isFinite(y)) ? y : null });
    }
    return out;
  }

  /** Insert a null point in the middle of a hole so Chart.js breaks the line. */
  function breakGaps(points, gapMs) {
    if (!gapMs || gapMs <= 0) return points;
    var out = [];
    var lastX = null;
    for (var i = 0; i < points.length; i++) {
      var p = points[i];
      if (lastX != null && p.x - lastX > gapMs) {
        out.push({ x: lastX + (p.x - lastX) / 2, y: null });
      }
      out.push(p);
      lastX = p.x;
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
    if (canvas) canvas.style.display = "none";
    if (empty) {
      if (message) empty.textContent = message;
      empty.classList.add("show");
    }
  }

  function showCanvas(canvasId) {
    var canvas = document.getElementById(canvasId);
    var empty = emptyFor(canvasId);
    if (canvas) canvas.style.display = "";
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

  /**
   * Draw one line chart.
   *
   * spec = {
   *   gapMs, markers: [{x,label}],
   *   series: [{label, unit, points:[{x,y}], color, step, axis:'right'}]
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
      var showPoints = vals.length <= 150;
      datasets.push({
        label: s.label || "",
        unit: s.unit || "",
        data: points,
        borderColor: color,
        backgroundColor: color,
        borderWidth: 1.7,
        pointRadius: vals.length === 1 ? 3.5 : (showPoints ? 1.8 : 0),
        pointBackgroundColor: color,
        pointBorderWidth: 0,
        pointHitRadius: 9,
        tension: s.step ? 0 : 0.15,
        stepped: s.step ? "before" : false,
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

    if (xMax === xMin) { xMin -= 60000; xMax += 60000; }
    var span = xMax - xMin;
    var narrow = (canvas.clientWidth || canvas.width || 0) < 480;

    function axisTitle(ds) {
      if (!ds.length) return undefined;
      var d = ds[0];
      return d.label + (d.unit ? " (" + d.unit + ")" : "");
    }
    var leftDs = datasets.filter(function (d) { return d.yAxisID === "y"; });
    var rightDs = datasets.filter(function (d) { return d.yAxisID === "y2"; });
    var yTitle = axisTitle(leftDs);
    var y2Title = axisTitle(rightDs);

    var scales = {
      x: {
        type: "linear",
        min: xMin,
        max: xMax,
        grid: { color: "#eef2f5", drawTicks: false },
        border: { color: "#cfd8e0" },
        ticks: {
          maxTicksLimit: narrow ? 4 : 8,
          font: { size: 10 },
          color: "#667",
          callback: function (value) {
            return fmtTick(Number(value), span, narrow);
          },
        },
      },
      y: {
        beginAtZero: !!(spec.beginAtZero),
        grid: { color: "#eef2f5" },
        border: { color: "#cfd8e0" },
        title: yTitle
          ? { display: true, text: yTitle, font: { size: 11 }, color: "#556" }
          : { display: false },
        ticks: {
          font: { size: 10 },
          color: "#667",
          callback: function (value) { return fmtNum(Number(value)); },
        },
      },
    };
    if (anyRight) {
      scales.y2 = {
        position: "right",
        grid: { drawOnChartArea: false },
        border: { color: "#cfd8e0" },
        title: y2Title
          ? { display: true, text: y2Title, font: { size: 11 }, color: "#556" }
          : { display: false },
        ticks: {
          font: { size: 10 },
          color: "#667",
          callback: function (value) { return fmtNum(Number(value)); },
        },
      };
    }

    var plugins = [markerPlugin(spec.markers || [])];

    return new Chart(canvas.getContext("2d"), {
      type: "line",
      data: { datasets: datasets },
      plugins: plugins,
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
            callbacks: {
              title: function (items) {
                return items.length ? fmtFull(items[0].parsed.x) : "";
              },
              label: function (item) {
                var unit = item.dataset.unit ? " " + item.dataset.unit : "";
                return item.dataset.label + ": " + fmtNum(item.parsed.y) + unit;
              },
            },
          },
        },
        scales: scales,
      },
    });
  }

  function summarize(points, unit) {
    var vals = finiteValues(points);
    if (!vals.length) return "";
    var min = vals[0], max = vals[0], sum = 0;
    for (var i = 0; i < vals.length; i++) {
      if (vals[i] < min) min = vals[i];
      if (vals[i] > max) max = vals[i];
      sum += vals[i];
    }
    var u = unit ? " " + unit : "";
    return '<span>n <strong>' + vals.length + "</strong></span>" +
      "<span>min <strong>" + fmtNum(min) + "</strong>" + u + "</span>" +
      "<span>max <strong>" + fmtNum(max) + "</strong>" + u + "</span>" +
      "<span>mean <strong>" + fmtNum(sum / vals.length) + "</strong>" + u + "</span>";
  }

  function displayValue(value) {
    if (value == null) return "—";
    if (typeof value === "object") {
      try { return JSON.stringify(value); } catch (e) { return String(value); }
    }
    return String(value);
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
    for (var k = 0; k < segs.length; k++) {
      var seg = segs[k];
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
      item.textContent = fmtFull(times[li]) + " — " +
        displayValue(vals[li]);
      list.appendChild(item);
    }
    if (n > shown) {
      var more = document.createElement("li");
      more.textContent = "… " + (n - shown) + " more transitions";
      list.appendChild(more);
    }
    container.appendChild(list);
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
            if (payload.unit) bits.push("(" + payload.unit + ")");
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

          var points = zipPoints(payload.ts, payload.value);
          if (!finiteValues(points).length) {
            if (canvas) canvas.style.display = "none";
            if (wrap) wrap.style.display = "none";
            if (empty) {
              empty.textContent = "No successful readings in the last " +
                currentDays() + " days.";
              empty.classList.add("show");
            }
            return;
          }
          if (summary) summary.innerHTML = summarize(points, payload.unit);
          current = lineChart("chart", {
            gapMs: gapMsFrom(data.gap_s),
            series: [{
              label: payload.name || key,
              unit: payload.unit,
              points: points,
            }],
          });
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
    var points = zipPoints(payload.ts, payload.value);
    if (!finiteValues(points).length) {
      if (canvas) canvas.style.display = "none";
      if (wrap) wrap.style.display = "none";
      if (empty) empty.classList.add("show");
      return;
    }
    var markers = (data.markers || []).map(function (m) {
      var x = Date.parse(m.ts);
      return { x: x, label: m.label };
    }).filter(function (m) { return Number.isFinite(m.x); });

    lineChart("chart", {
      gapMs: gapMsFrom(data.gap_s),
      markers: markers,
      series: [{
        label: payload.name || payload.key,
        unit: payload.unit,
        points: points,
      }],
    });
  }

  function initOverview() {
    var data = readJSON("overview-data");
    if (!data || !data.chart) return;
    var c = data.chart;
    var gap = gapMsFrom(data.gap_s);
    var soc = zipPoints(c.ts, c.soc);
    var pack = zipPoints(c.ts, c.pack_v);
    if (!finiteValues(soc).length && !finiteValues(pack).length) {
      showEmpty("overviewChart");
      return;
    }
    lineChart("overviewChart", {
      gapMs: gap,
      series: [
        { label: "State of charge", unit: "%", points: soc, color: "#1e8449" },
        { label: "Pack voltage", unit: "V", points: pack, color: "#2471a3",
          axis: "right" },
      ],
    });
  }

  function seriesChart(canvasId, ts, values, label, unit, gapMs) {
    var points = zipPoints(ts, values);
    if (!finiteValues(points).length) {
      showEmpty(canvasId);
      return;
    }
    lineChart(canvasId, {
      gapMs: gapMs,
      series: [{ label: label, unit: unit, points: points }],
    });
  }

  function initBattery() {
    var data = readJSON("battery-data");
    if (!data) return;
    var gap = gapMsFrom(data.gap_s);
    var c = data.chart || {};
    seriesChart("deltaChart", c.ts, c.delta_mv, "Cell delta", "mV", gap);
    seriesChart("socChart", c.ts, c.soc, "State of charge", "%", gap);
    seriesChart("packChart", c.ts, c.pack_v, "Pack voltage", "V", gap);
    seriesChart("tempChart", c.ts, c.temp, "Battery temperature", "°C", gap);
    if (data.lv && data.lv.ts && data.lv.ts.length) {
      seriesChart("lvChart", data.lv.ts, data.lv.v, "12 V system", "V", gap);
    }
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
          x: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
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
    initOverview();
    initBattery();
    initProgress();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
