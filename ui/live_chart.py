"""Animated time-series charts that replay a simulated run as if it were happening live.

Why: a finished line chart shows the result; replaying it shows the *behaviour*: you watch demand
arrive and see SCALIX add workers before Threshold does. The data are exactly the simulator's time
series; only the drawing is animated.

How it works:

* ``live_chart_html`` turns series of (x, y) points into one self-contained HTML page (an SVG chart
  drawn by a small script, no external libraries, so it also works offline). Streamlit shows it
  with ``components.html``.
* The replay starts only when at least half of the chart is visible on screen
  (IntersectionObserver), pauses while it is scrolled away or the browser tab is hidden, and
  finishes within ``duration_s`` (at most 30 s). A vertical "now" line and a clock show the
  simulated time; each line's current value is labelled at its tip.
* "Replay" restarts it, "Show all" jumps to the end. Users who ask their system for reduced motion
  get the finished chart straight away.
* Hovering shows every series' value at that time (only up to the time drawn so far).

Pure functions of plain lists: no Streamlit in ``live_chart_html``, so it is easy to test.
"""

from __future__ import annotations

import html as _html
import json
import math
from dataclasses import dataclass, field
from typing import Sequence

MAX_DURATION_S = 30.0
DEFAULT_DURATION_S = 20.0


@dataclass(frozen=True)
class Series:
    name: str
    x: Sequence[float]
    y: Sequence[float]
    color: str
    width: float = 2.0
    step: bool = False           # draw as steps (value holds until the next point)
    label_tip: bool = True       # label the current value at the line's tip


@dataclass(frozen=True)
class ChartSpec:
    title: str
    y_label: str
    series: tuple[Series, ...]
    x_label: str = "time (minutes)"
    y_min: float = 0.0
    y_max: float | None = None
    y_format: str = "int"        # "int", "1f" or "pct"
    unit: str = ""               # appended to tooltip values, e.g. " workers"
    ref_line: tuple[float, str] | None = None   # (y, label)
    height: int = 340
    duration_s: float = DEFAULT_DURATION_S
    caption: str = ""
    time_unit: str = "min"       # "min" or "h": how the clock and the replay note name the x values
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.series:
            raise ValueError("a live chart needs at least one series")
        if not 1.0 <= self.duration_s <= MAX_DURATION_S:
            raise ValueError(f"duration_s must be between 1 and {MAX_DURATION_S:g} seconds, got {self.duration_s!r}")
        if self.time_unit not in ("min", "h"):
            raise ValueError("time_unit must be 'min' or 'h'")
        if self.y_format not in ("int", "1f", "pct"):
            raise ValueError("y_format must be 'int', '1f' or 'pct'")
        for s in self.series:
            if len(s.x) != len(s.y) or len(s.x) < 2:
                raise ValueError(f"series {s.name!r} needs matching x and y with at least 2 points")


def _clean(values: Sequence[float]) -> list[float | None]:
    out = []
    for v in values:
        v = float(v)
        out.append(None if math.isnan(v) or math.isinf(v) else round(v, 6))
    return out


def payload(spec: ChartSpec) -> dict:
    """The data the script draws: exactly the given points, nothing invented."""
    xs = [float(v) for s in spec.series for v in s.x]
    ys = [float(v) for s in spec.series for v in s.y if not math.isnan(float(v))]
    y_max = spec.y_max if spec.y_max is not None else (max(ys) if ys else 1.0) * 1.12
    if spec.ref_line:
        y_max = max(y_max, spec.ref_line[0] * 1.08)
    return {
        "title": spec.title, "yLabel": spec.y_label, "xLabel": spec.x_label,
        "xMin": min(xs), "xMax": max(xs), "yMin": spec.y_min, "yMax": max(y_max, spec.y_min + 1e-9),
        "yFormat": spec.y_format, "unit": spec.unit, "durationMs": int(spec.duration_s * 1000),
        "ref": None if spec.ref_line is None else {"y": spec.ref_line[0], "label": spec.ref_line[1]},
        "caption": spec.caption, "tUnit": spec.time_unit,
        "tWord": "simulated minutes" if spec.time_unit == "min" else "simulated hours",
        "series": [{"name": s.name, "x": _clean(s.x), "y": _clean(s.y), "color": s.color, "width": s.width,
                    "step": s.step, "tip": s.label_tip} for s in spec.series],
    }


def live_chart_html(spec: ChartSpec) -> str:
    data = json.dumps(payload(spec), separators=(",", ":")).replace("</", "<\\/")
    title = _html.escape(spec.title)
    return _TEMPLATE.replace("__TITLE__", title).replace("__DATA__", data).replace("__HEIGHT__", str(spec.height))


def show(spec: ChartSpec) -> None:
    """Render in Streamlit. The iframe is re-created only when the data change, so reruns don't restart it."""
    import streamlit.components.v1 as components

    components.html(live_chart_html(spec), height=spec.height + 8)


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --surface:#fbfaf7; --text:#1f2328; --muted:#52514e; --faint:#8a877f; --grid:#e9e5dc; --axis:#d8d4ca; }
  html, body { margin:0; padding:0; background:var(--surface); }
  body { font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif; color:var(--muted); font-size:12px; }
  .wrap { position:relative; height:__HEIGHT__px; }
  .head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; padding:2px 4px 0 4px; }
  .title { font-size:15px; color:var(--text); }
  .meta { font-size:12px; color:var(--faint); margin-top:2px; min-height:16px; }
  .controls { display:flex; gap:6px; flex-shrink:0; }
  button { font:inherit; font-size:12px; color:var(--muted); background:transparent; border:1px solid var(--axis);
           border-radius:6px; padding:3px 9px; cursor:pointer; }
  button:hover { color:var(--text); border-color:var(--faint); }
  button:focus-visible { outline:2px solid #0f766e; outline-offset:1px; }
  .legend { display:flex; flex-wrap:wrap; gap:4px 14px; padding:6px 4px 0 4px; }
  .legend span { display:inline-flex; align-items:center; gap:6px; color:var(--muted); }
  .legend i { display:inline-block; width:14px; height:2px; border-radius:1px; }
  svg { display:block; overflow:visible; }
  .tip { position:absolute; pointer-events:none; background:#fff; border:1px solid var(--axis); border-radius:6px;
         padding:6px 8px; font-size:12px; color:var(--text); box-shadow:0 2px 8px rgba(0,0,0,.08); display:none;
         white-space:nowrap; z-index:2; }
  .tip b { font-weight:600; }
  .tip .row { display:flex; align-items:center; gap:6px; }
  .tip i { width:8px; height:8px; border-radius:50%; display:inline-block; }
</style></head>
<body>
<div class="wrap" id="wrap" role="img" aria-label="__TITLE__">
  <div class="head">
    <div><div class="title">__TITLE__</div><div class="meta" id="meta"></div></div>
    <div class="controls"><button id="replay" type="button">Replay</button><button id="skip" type="button">Show all</button></div>
  </div>
  <div class="legend" id="legend"></div>
  <svg id="svg"></svg>
  <div class="tip" id="tip"></div>
</div>
<script>
(function () {
  const D = __DATA__;
  const NS = "http://www.w3.org/2000/svg";
  const wrap = document.getElementById("wrap"), svg = document.getElementById("svg");
  const tip = document.getElementById("tip"), meta = document.getElementById("meta");
  const legend = document.getElementById("legend");
  const named = D.series.filter(s => s.tip).length > 1;   // several tips: say whose value each one is
  const M = { l: 62, r: named ? 112 : 56, t: 10, b: 40 };
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  D.series.forEach(s => {
    const el = document.createElement("span");
    el.innerHTML = '<i style="background:' + s.color + '"></i>' + s.name.replace(/</g, "&lt;");
    legend.appendChild(el);
  });

  function fmt(v) {
    if (v === null || v === undefined) return "–";
    if (D.yFormat === "pct") return Math.round(v * 100) + "%";
    if (D.yFormat === "1f") return v.toFixed(1);
    return String(Math.round(v));
  }
  function niceTicks(lo, hi, n) {
    const span = hi - lo, raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag;
    const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    (parent || svg).appendChild(e); return e;
  }

  let W = 0, H = 0, sx, sy, paths = [], tips = [], nowLine, nowLabel;
  function layout() {
    W = wrap.clientWidth; H = wrap.clientHeight - svg.getBoundingClientRect().top + wrap.getBoundingClientRect().top;
    svg.setAttribute("width", W); svg.setAttribute("height", Math.max(120, H));
    H = Math.max(120, H);
    svg.innerHTML = ""; paths = []; tips = [];
    const pw = W - M.l - M.r, ph = H - M.t - M.b;
    sx = x => M.l + (x - D.xMin) / (D.xMax - D.xMin || 1) * pw;
    sy = y => M.t + ph - (y - D.yMin) / (D.yMax - D.yMin) * ph;
    niceTicks(D.yMin, D.yMax, 5).forEach(v => {
      el("line", { x1: M.l, x2: M.l + pw, y1: sy(v), y2: sy(v), stroke: "#e9e5dc", "stroke-width": 1 });
      const t = el("text", { x: M.l - 8, y: sy(v) + 4, "text-anchor": "end", fill: "#52514e", "font-size": 11 });
      t.textContent = fmt(v);
    });
    niceTicks(D.xMin, D.xMax, Math.max(3, Math.floor(pw / 90))).forEach(v => {
      el("line", { x1: sx(v), x2: sx(v), y1: M.t + ph, y2: M.t + ph + 4, stroke: "#d8d4ca" });
      const t = el("text", { x: sx(v), y: M.t + ph + 17, "text-anchor": "middle", fill: "#52514e", "font-size": 11 });
      t.textContent = (+v.toFixed(2)).toString();
    });
    el("line", { x1: M.l, x2: M.l + pw, y1: M.t + ph, y2: M.t + ph, stroke: "#d8d4ca" });
    const xl = el("text", { x: M.l + pw / 2, y: M.t + ph + 34, "text-anchor": "middle", fill: "#52514e", "font-size": 12 });
    xl.textContent = D.xLabel;
    const yl = el("text", { x: 14, y: M.t + ph / 2, "text-anchor": "middle", fill: "#52514e", "font-size": 12,
                            transform: "rotate(-90 14 " + (M.t + ph / 2) + ")" });
    yl.textContent = D.yLabel;
    if (D.ref) {
      el("line", { x1: M.l, x2: M.l + pw, y1: sy(D.ref.y), y2: sy(D.ref.y), stroke: "#8a877f", "stroke-width": 1,
                   "stroke-dasharray": "4 4" });
      const rt = el("text", { x: M.l + pw - 4, y: sy(D.ref.y) - 5, "text-anchor": "end", fill: "#52514e", "font-size": 11 });
      rt.textContent = D.ref.label;
    }
    nowLine = el("line", { y1: M.t, y2: M.t + ph, stroke: "#8a877f", "stroke-width": 1, opacity: 0 });
    D.series.forEach(s => {
      paths.push(el("path", { fill: "none", stroke: s.color, "stroke-width": s.width, "stroke-linejoin": "round",
                              "stroke-linecap": "round" }));
    });
    D.series.forEach(s => {
      const dot = el("circle", { r: 3.5, fill: s.color, stroke: "#fbfaf7", "stroke-width": 2, opacity: 0 });
      const lab = el("text", { "font-size": 12, fill: "#1f2328", opacity: 0 });
      tips.push({ dot, lab, show: s.tip });
    });
    nowLabel = el("text", { y: M.t + 12, "font-size": 11, fill: "#52514e", opacity: 0 });
    draw(cur);
  }

  // value of series s at time t (steps hold; lines interpolate), and its path up to t
  function valueAt(s, t) {
    const x = s.x, y = s.y; if (t < x[0]) return null;
    let i = 0; while (i + 1 < x.length && x[i + 1] <= t) i++;
    if (s.step || i + 1 >= x.length || y[i] === null || y[i + 1] === null) return y[i];
    const f = (t - x[i]) / (x[i + 1] - x[i]); return y[i] + f * (y[i + 1] - y[i]);
  }
  function pathTo(s, t) {
    let d = "", pen = false, prevY = null;
    for (let i = 0; i < s.x.length && s.x[i] <= t; i++) {
      const yv = s.y[i]; if (yv === null) { pen = false; continue; }
      const X = sx(s.x[i]).toFixed(1), Y = sy(yv).toFixed(1);
      if (!pen) { d += "M" + X + " " + Y; pen = true; }
      else if (s.step) { d += "H" + X + "V" + Y; }
      else { d += "L" + X + " " + Y; }
      prevY = yv;
    }
    const v = valueAt(s, t);
    if (pen && v !== null) d += (s.step ? "H" + sx(t).toFixed(1) : "L" + sx(t).toFixed(1) + " " + sy(v).toFixed(1));
    return d;
  }

  let cur = D.xMin, done = false;
  function draw(t) {
    if (!sx) return;
    cur = t;
    D.series.forEach((s, k) => paths[k].setAttribute("d", pathTo(s, t)));
    const playing = t > D.xMin && t < D.xMax;
    nowLine.setAttribute("x1", sx(t)); nowLine.setAttribute("x2", sx(t)); nowLine.setAttribute("opacity", playing ? 0.7 : 0);
    // tip labels with a simple de-overlap (at least 14 px apart)
    const items = D.series.map((s, k) => ({ k, v: valueAt(s, t) })).filter(o => o.v !== null && tips[o.k].show);
    items.sort((a, b) => sy(a.v) - sy(b.v));
    // label positions: keep at least 14 px apart, and inside the plot (shift the stack up if it overflows)
    let last = -1e9;
    items.forEach(o => { o.ly = Math.max(sy(o.v) + 4, last + 14); last = o.ly; });
    const bottom = sy(D.yMin) - 2, over = items.length ? items[items.length - 1].ly - bottom : 0;
    if (over > 0) items.forEach(o => { o.ly -= over; });
    tips.forEach(o => { o.dot.setAttribute("opacity", 0); o.lab.setAttribute("opacity", 0); });
    items.forEach(o => {
      const T = tips[o.k], yPix = sy(o.v), ly = o.ly;
      T.dot.setAttribute("cx", sx(t)); T.dot.setAttribute("cy", yPix); T.dot.setAttribute("opacity", 1);
      T.lab.setAttribute("x", sx(t) + 8); T.lab.setAttribute("y", ly); T.lab.setAttribute("opacity", 1);
      T.lab.textContent = (named ? D.series[o.k].name + " " : "") + fmt(o.v);
    });
    nowLabel.setAttribute("opacity", playing ? 1 : 0);
    nowLabel.setAttribute("x", Math.min(sx(t) + 6, W - M.r));
    nowLabel.textContent = "t = " + t.toFixed(1) + " " + D.tUnit;
    const span = D.xMax - D.xMin;
    meta.textContent = done || t >= D.xMax
      ? "Replay finished: " + (+span.toFixed(1)) + " " + D.tWord + ". Hover to read values."
      : (t <= D.xMin ? "Replay starts when this chart is on screen." :
         "Replaying " + (+span.toFixed(1)) + " " + D.tWord + " in " + Math.round(D.durationMs / 1000) + " s · now t = " + t.toFixed(1) + " " + D.tUnit);
  }

  // ------------------------------------------------------------ playback: starts when visible
  let elapsed = 0, lastTs = null, visible = false, raf = null, started = false;
  function frame(ts) {
    raf = null;
    if (!visible || document.hidden) { lastTs = null; return; }
    if (lastTs !== null) elapsed += Math.min(ts - lastTs, 100);   // hidden or slow frames never jump ahead
    lastTs = ts;
    const p = Math.min(1, elapsed / D.durationMs);
    const eased = p;   // constant speed: simulated time advances evenly, like a clock
    draw(D.xMin + eased * (D.xMax - D.xMin));
    if (p < 1) raf = requestAnimationFrame(frame); else { done = true; draw(D.xMax); }
  }
  function play() { if (!done && raf === null && visible && !document.hidden) { lastTs = null; raf = requestAnimationFrame(frame); } }
  function finish() { done = true; elapsed = D.durationMs; if (raf) cancelAnimationFrame(raf); raf = null; draw(D.xMax); }
  document.getElementById("skip").onclick = finish;
  document.getElementById("replay").onclick = () => { done = false; elapsed = 0; if (raf) cancelAnimationFrame(raf); raf = null;
                                                     draw(D.xMin); started = true; play(); };
  document.addEventListener("visibilitychange", play);

  if (reduce) { done = true; }
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(entries => {
      entries.forEach(e => {
        visible = e.isIntersecting && e.intersectionRatio >= 0.5;
        if (visible) { started = true; play(); }
      });
    }, { threshold: [0, 0.5, 1] }).observe(wrap);
  } else { visible = true; started = true; }

  // ------------------------------------------------------------ hover
  wrap.addEventListener("mousemove", ev => {
    const r = svg.getBoundingClientRect(), mx = ev.clientX - r.left;
    if (!sx || mx < M.l || mx > W - M.r) { tip.style.display = "none"; return; }
    const t = D.xMin + (mx - M.l) / (W - M.l - M.r) * (D.xMax - D.xMin);
    if (t > cur + 1e-9) { tip.style.display = "none"; return; }
    let rows = '<div><b>t = ' + t.toFixed(1) + ' ' + D.tUnit + '</b></div>';
    D.series.forEach(s => {
      // the value in effect at t (latest point at or before t), not interpolated, so it matches the data
      let i = 0; while (i + 1 < s.x.length && s.x[i + 1] <= t) i++;
      rows += '<div class="row"><i style="background:' + s.color + '"></i>' + s.name.replace(/</g, "&lt;") + ': <b>' +
              fmt(s.y[i]) + D.unit + '</b></div>';
    });
    tip.innerHTML = rows; tip.style.display = "block";
    const wr = wrap.getBoundingClientRect();
    let left = ev.clientX - wr.left + 14; if (left + tip.offsetWidth > W) left = ev.clientX - wr.left - tip.offsetWidth - 14;
    tip.style.left = left + "px"; tip.style.top = Math.max(0, ev.clientY - wr.top - 10) + "px";
  });
  wrap.addEventListener("mouseleave", () => { tip.style.display = "none"; });

  if ("ResizeObserver" in window) new ResizeObserver(layout).observe(wrap); else window.addEventListener("resize", layout);
  layout();
  if (reduce) draw(D.xMax); else draw(D.xMin);
})();
</script>
</body></html>
"""
