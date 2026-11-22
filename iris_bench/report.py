"""Terminal, Markdown and self-contained offline HTML graphical comparison reports."""

from __future__ import annotations

import json
from pathlib import Path

from iris_bench import DEFAULT_REPORT_HTML, DEFAULT_RESULTS_DIR
from iris_bench.models import PublicBenchmarkResult


def load_results(results_dir: Path = DEFAULT_RESULTS_DIR) -> list[PublicBenchmarkResult]:
    """Load and sort all public benchmark JSON files by overall_iris_score descending."""
    resolved = results_dir.expanduser().resolve()
    if not resolved.is_dir():
        return []
    loaded: list[PublicBenchmarkResult] = []
    for path in sorted(resolved.glob("*.json")):
        loaded.append(PublicBenchmarkResult.model_validate_json(path.read_text(encoding="utf-8")))
    loaded.sort(
        key=lambda r: (r.scores.overall_iris_score, r.scores.capability_score, r.timestamp_utc),
        reverse=True,
    )
    return loaded


def _fmt_num(val: float | None, suffix: str = "") -> str:
    if val is None:
        return "—"
    return f"{val:.1f}{suffix}"


def format_terminal_table(results: list[PublicBenchmarkResult]) -> str:
    """Format a compact leaderboard table for stdout."""
    if not results:
        return "No benchmark results found."
    headers = [
        "Engine",
        "Model",
        "Overall",
        "Cap%",
        "Judge%",
        "Voice%",
        "TTFT(ms)",
        "TTFS(ms)",
        "tok/s",
        "Comments",
    ]
    rows: list[list[str]] = []
    for r in results:
        tps = r.speed.median_tok_per_sec
        tps_str = (
            f"{tps:.1f}"
            if tps is not None
            else (
                f"~{r.speed.median_est_tok_per_sec:.1f}"
                if r.speed.median_est_tok_per_sec is not None
                else "—"
            )
        )
        comment = (r.comments[:28] + "…") if len(r.comments) > 29 else (r.comments or "—")
        rows.append(
            [
                r.engine,
                r.model,
                _fmt_num(r.scores.overall_iris_score),
                _fmt_num(r.scores.capability_score),
                _fmt_num(r.scores.judge_quality_score),
                _fmt_num(r.scores.voice_responsiveness_score),
                _fmt_num(r.speed.median_ttft_ms),
                _fmt_num(r.speed.median_ttfs_ms),
                tps_str,
                comment,
            ]
        )
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    sep = "  "
    line_hdr = sep.join(headers[i].ljust(widths[i]) for i in range(len(headers)))
    line_div = sep.join("-" * widths[i] for i in range(len(headers)))
    body = [sep.join(row[i].ljust(widths[i]) for i in range(len(headers))) for row in rows]
    return "\n".join([line_hdr, line_div, *body])


def format_markdown_table(results: list[PublicBenchmarkResult]) -> str:
    """Format a GitHub-flavoured Markdown comparison table."""
    if not results:
        return "No benchmark results found."
    lines = [
        "| Engine | Model | Overall | Capability | Judge | Voice Resp. "
        "| TTFT (ms) | TTFS (ms) | tok/s | Comments |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in results:
        tps = r.speed.median_tok_per_sec
        tps_str = (
            f"{tps:.1f}"
            if tps is not None
            else (
                f"~{r.speed.median_est_tok_per_sec:.1f}"
                if r.speed.median_est_tok_per_sec is not None
                else "—"
            )
        )
        comment = r.comments.replace("|", "\\|") if r.comments else "—"
        lines.append(
            "| "
            + " | ".join(
                [
                    r.engine.replace("|", "\\|"),
                    r.model.replace("|", "\\|"),
                    _fmt_num(r.scores.overall_iris_score),
                    _fmt_num(r.scores.capability_score, "%"),
                    _fmt_num(r.scores.judge_quality_score, "%"),
                    _fmt_num(r.scores.voice_responsiveness_score),
                    _fmt_num(r.speed.median_ttft_ms),
                    _fmt_num(r.speed.median_ttfs_ms),
                    tps_str,
                    comment,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Iris Bench — Model &amp; Engine Comparison</title>
<style>
  :root {
    --bg: #0f131a;
    --panel: #171d27;
    --panel-alt: #1e2633;
    --border: #2a3444;
    --text: #e7edf5;
    --muted: #9aa7b8;
    --accent: #4fb3ff;
    --green: #42d392;
    --amber: #f6b73c;
    --coral: #ff6b6b;
    --violet: #a78bfa;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 24px;
    font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.45;
  }
  .wrap { max-width: 1280px; margin: 0 auto; }
  h1 { margin: 0 0 6px; font-size: 1.75rem; }
  h2 { margin: 0 0 12px; font-size: 1.15rem; color: var(--text); }
  .subtitle { color: var(--muted); margin: 0 0 22px; font-size: 0.95rem; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(460px, 1fr));
    gap: 18px;
    margin-bottom: 18px;
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 18px;
  }
  .table-scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { padding: 9px 10px; border-bottom: 1px solid var(--border); text-align: left; }
  th {
    color: var(--muted);
    font-weight: 600;
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
  }
  th:hover { color: var(--accent); }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tr:hover td { background: var(--panel-alt); }
  .pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 999px;
    font-weight: 600;
    font-size: 0.82rem;
  }
  .pill-hi { background: rgba(66, 211, 146, 0.16); color: var(--green); }
  .pill-mid { background: rgba(246, 183, 60, 0.16); color: var(--amber); }
  .pill-lo { background: rgba(255, 107, 107, 0.16); color: var(--coral); }
  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    font-size: 0.82rem;
    color: var(--muted);
    margin-bottom: 10px;
  }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .dot { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
  .heat-cell {
    text-align: center;
    font-variant-numeric: tabular-nums;
    font-weight: 600;
    border-radius: 4px;
  }
  .comment-cell { max-width: 280px; color: var(--muted); font-size: 0.84rem; }
  svg { width: 100%; height: auto; display: block; }
  .empty { color: var(--muted); padding: 18px 0; text-align: center; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Iris Bench Comparison Report</h1>
  <p class="subtitle" id="meta-line"></p>

  <div class="card">
    <h2>Leaderboard (click any column header to sort)</h2>
    <div class="table-scroll">
      <table id="leaderboard">
        <thead>
          <tr>
            <th data-key="engine">Engine</th>
            <th data-key="model">Model</th>
            <th class="num" data-key="overall">Overall</th>
            <th class="num" data-key="cap">Capability %</th>
            <th class="num" data-key="judge">Judge %</th>
            <th class="num" data-key="voice">Voice Resp.</th>
            <th class="num" data-key="ttft">TTFT (ms)</th>
            <th class="num" data-key="ttfs">TTFS (ms)</th>
            <th class="num" data-key="tps">tok/s</th>
            <th data-key="hw">Hardware</th>
            <th data-key="comments">Comments</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Composite &amp; Sub-Scores</h2>
      <div class="legend">
        <span><i class="dot" style="background:var(--accent)"></i>Overall</span>
        <span><i class="dot" style="background:var(--green)"></i>Capability %</span>
        <span><i class="dot" style="background:var(--violet)"></i>Judge %</span>
        <span><i class="dot" style="background:var(--amber)"></i>Voice Resp.</span>
      </div>
      <div id="chart-scores"></div>
    </div>

    <div class="card">
      <h2>Voice Latency (TTFS) vs. Capability Frontier</h2>
      <div id="chart-scatter"></div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Speed &amp; Throughput (TTFT / TTFS / tok/s)</h2>
      <div class="legend">
        <span><i class="dot" style="background:var(--accent)"></i>Median TTFT (ms)</span>
        <span><i class="dot" style="background:var(--amber)"></i>Median TTFS (ms)</span>
        <span><i class="dot" style="background:var(--green)"></i>Tokens/sec</span>
      </div>
      <div id="chart-speed"></div>
    </div>

    <div class="card">
      <h2>Long-Conversation Context Growth (TTFS across turns 1–6)</h2>
      <div id="chart-context"></div>
    </div>
  </div>

  <div class="card">
    <h2>Capability Category Heatmap</h2>
    <div class="table-scroll" id="heatmap-wrap"></div>
  </div>
</div>

<script>
const RESULTS = __IRIS_BENCH_DATA__;
const PALETTE = [
  "#4fb3ff", "#42d392", "#f6b73c", "#a78bfa",
  "#ff6b6b", "#2dd4bf", "#f472b6", "#facc15"
];

function esc(s) {
  const map = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"};
  return String(s ?? "").replace(/[&<>"]/g, c => map[c]);
}
function labelOf(r) { return `${r.engine}: ${r.model}`; }
function effTps(r) {
  return r.speed.median_tok_per_sec ?? r.speed.median_est_tok_per_sec ?? null;
}
function pill(val) {
  if (val == null) return "—";
  const cls = val >= 80 ? "pill-hi" : val >= 55 ? "pill-mid" : "pill-lo";
  return `<span class="pill ${cls}">${val.toFixed(1)}</span>`;
}
function fmt(val, suffix = "") {
  return val == null ? "—" : `${val.toFixed(1)}${suffix}`;
}

const rows = RESULTS.map((r, i) => ({
  idx: i,
  raw: r,
  engine: r.engine,
  model: r.model,
  overall: r.scores.overall_iris_score,
  cap: r.scores.capability_score,
  judge: r.scores.judge_quality_score,
  voice: r.scores.voice_responsiveness_score,
  ttft: r.speed.median_ttft_ms,
  ttfs: r.speed.median_ttfs_ms,
  tps: effTps(r),
  hw: [r.hardware.gpu_name, r.hardware.cpu].filter(Boolean).join(" / ") || r.hardware.os,
  comments: r.comments || ""
}));

let sortKey = "overall";
let sortAsc = false;

function renderTable() {
  const sorted = [...rows].sort((a, b) => {
    const va = a[sortKey], vb = b[sortKey];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === "number" && typeof vb === "number") return sortAsc ? va - vb : vb - va;
    return sortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
  });
  const tbody = document.querySelector("#leaderboard tbody");
  if (!sorted.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="empty">No benchmark JSON files found.</td></tr>';
    return;
  }
  tbody.innerHTML = sorted.map(row => {
    const rawTps = row.raw.speed.median_tok_per_sec;
    const estTps = row.raw.speed.median_est_tok_per_sec;
    const tpsText = rawTps != null
      ? rawTps.toFixed(1)
      : (estTps != null ? `~${estTps.toFixed(1)}` : "—");
    return `<tr>
      <td>${esc(row.engine)}</td>
      <td><strong>${esc(row.model)}</strong></td>
      <td class="num">${pill(row.overall)}</td>
      <td class="num">${fmt(row.cap, "%")}</td>
      <td class="num">${fmt(row.judge, "%")}</td>
      <td class="num">${fmt(row.voice)}</td>
      <td class="num">${fmt(row.ttft)}</td>
      <td class="num">${fmt(row.ttfs)}</td>
      <td class="num">${tpsText}</td>
      <td>${esc(row.hw)}</td>
      <td class="comment-cell">${esc(row.comments || "—")}</td>
    </tr>`;
  }).join("");
}

document.querySelectorAll("#leaderboard th[data-key]").forEach(th => {
  th.addEventListener("click", () => {
    const k = th.dataset.key;
    if (sortKey === k) {
      sortAsc = !sortAsc;
    } else {
      sortKey = k;
      sortAsc = (k === "engine" || k === "model" || k === "ttft" || k === "ttfs");
    }
    renderTable();
  });
});

function renderScoresChart() {
  const el = document.getElementById("chart-scores");
  if (!RESULTS.length) { el.innerHTML = '<div class="empty">No data</div>'; return; }
  const barH = 11, gap = 3, rowH = 66, left = 180, w = 560, right = 44;
  const plotW = w - left - right;
  const h = RESULTS.length * rowH + 20;
  let svg = `<svg viewBox="0 0 ${w} ${h}">`;
  for (let t = 0; t <= 100; t += 25) {
    const x = left + (t / 100) * plotW;
    svg += `<line x1="${x}" y1="0" x2="${x}" y2="${h - 16}" `
      + `stroke="#2a3444" stroke-dasharray="2,3"/>`;
    svg += `<text x="${x}" y="${h - 3}" fill="#9aa7b8" font-size="10" `
      + `text-anchor="middle">${t}</text>`;
  }
  RESULTS.forEach((r, i) => {
    const y0 = i * rowH + 6;
    const lbl = esc(labelOf(r).slice(0, 28));
    svg += `<text x="${left - 8}" y="${y0 + 28}" fill="#e7edf5" font-size="11" `
      + `text-anchor="end">${lbl}</text>`;
    const vals = [
      [r.scores.overall_iris_score, "var(--accent)"],
      [r.scores.capability_score, "var(--green)"],
      [r.scores.judge_quality_score, "var(--violet)"],
      [r.scores.voice_responsiveness_score, "var(--amber)"]
    ];
    vals.forEach(([v, col], j) => {
      const y = y0 + j * (barH + gap);
      if (v == null) return;
      const bw = Math.max(2, (v / 100) * plotW);
      svg += `<rect x="${left}" y="${y}" width="${bw}" height="${barH}" rx="3" fill="${col}"/>`;
      svg += `<text x="${left + bw + 5}" y="${y + 9}" fill="#9aa7b8" `
        + `font-size="9">${v.toFixed(1)}</text>`;
    });
  });
  svg += "</svg>";
  el.innerHTML = svg;
}

function renderScatterChart() {
  const el = document.getElementById("chart-scatter");
  const pts = RESULTS.map((r, i) => ({
    label: labelOf(r),
    x: r.speed.median_ttfs_ms ?? r.speed.median_ttft_ms ?? r.speed.median_model_ms,
    y: r.scores.capability_score,
    color: PALETTE[i % PALETTE.length]
  })).filter(p => p.x != null);
  if (!pts.length) { el.innerHTML = '<div class="empty">No timing data</div>'; return; }
  const w = 560, h = 280, padL = 48, padR = 24, padT = 20, padB = 38;
  const maxX = Math.max(1000, ...pts.map(p => p.x)) * 1.15;
  let svg = `<svg viewBox="0 0 ${w} ${h}">`;
  for (let yv = 0; yv <= 100; yv += 25) {
    const y = padT + (1 - yv / 100) * (h - padT - padB);
    svg += `<line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" `
      + `stroke="#2a3444" stroke-dasharray="2,3"/>`;
    svg += `<text x="${padL - 6}" y="${y + 3}" fill="#9aa7b8" font-size="10" `
      + `text-anchor="end">${yv}%</text>`;
  }
  svg += `<text x="${(padL + w - padR) / 2}" y="${h - 6}" fill="#9aa7b8" font-size="10" `
    + `text-anchor="middle">Median Time to First Sentence (ms — lower is faster)</text>`;
  pts.forEach(p => {
    const cx = padL + (p.x / maxX) * (w - padL - padR);
    const cy = padT + (1 - p.y / 100) * (h - padT - padB);
    svg += `<circle cx="${cx}" cy="${cy}" r="6" fill="${p.color}" `
      + `stroke="#0f131a" stroke-width="1.5"/>`;
    svg += `<text x="${cx + 8}" y="${cy + 3}" fill="#e7edf5" `
      + `font-size="10">${esc(p.label.slice(0, 22))}</text>`;
  });
  svg += "</svg>";
  el.innerHTML = svg;
}

function renderSpeedChart() {
  const el = document.getElementById("chart-speed");
  if (!RESULTS.length) { el.innerHTML = '<div class="empty">No data</div>'; return; }
  const msVals = RESULTS.flatMap(r => [r.speed.median_ttft_ms || 0, r.speed.median_ttfs_ms || 0]);
  const maxMs = Math.max(500, ...msVals);
  const maxTps = Math.max(20, ...RESULTS.map(r => effTps(r) || 0));
  const rowH = 54, left = 180, w = 560, right = 60;
  const plotW = w - left - right;
  const h = RESULTS.length * rowH + 14;
  let svg = `<svg viewBox="0 0 ${w} ${h}">`;
  RESULTS.forEach((r, i) => {
    const y0 = i * rowH + 6;
    const lbl = esc(labelOf(r).slice(0, 28));
    svg += `<text x="${left - 8}" y="${y0 + 24}" fill="#e7edf5" font-size="11" `
      + `text-anchor="end">${lbl}</text>`;
    const bars = [
      [r.speed.median_ttft_ms, maxMs, "var(--accent)", "ms"],
      [r.speed.median_ttfs_ms, maxMs, "var(--amber)", "ms"],
      [effTps(r), maxTps, "var(--green)", " tok/s"]
    ];
    bars.forEach(([val, maxV, col, unit], j) => {
      if (val == null) return;
      const y = y0 + j * 14;
      const bw = Math.max(2, (val / maxV) * plotW);
      svg += `<rect x="${left}" y="${y}" width="${bw}" height="10" rx="3" fill="${col}"/>`;
      svg += `<text x="${left + bw + 5}" y="${y + 8}" fill="#9aa7b8" `
        + `font-size="9">${val.toFixed(0)}${unit}</text>`;
    });
  });
  svg += "</svg>";
  el.innerHTML = svg;
}

function renderContextChart() {
  const el = document.getElementById("chart-context");
  const series = RESULTS.map((r, i) => ({
    label: labelOf(r),
    vals: r.speed.long_conversation_ttfs_ms || [],
    color: PALETTE[i % PALETTE.length]
  })).filter(s => s.vals.some(v => v != null));
  if (!series.length) {
    el.innerHTML = '<div class="empty">No long-conversation timing data</div>';
    return;
  }
  const w = 560, h = 260, padL = 52, padR = 24, padT = 18, padB = 34;
  const maxTurns = Math.max(2, ...series.map(s => s.vals.length));
  const maxY = Math.max(500, ...series.flatMap(s => s.vals.filter(v => v != null))) * 1.1;
  let svg = `<svg viewBox="0 0 ${w} ${h}">`;
  for (let step = 0; step <= 4; step++) {
    const val = (maxY * step) / 4;
    const y = padT + (1 - step / 4) * (h - padT - padB);
    svg += `<line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" `
      + `stroke="#2a3444" stroke-dasharray="2,3"/>`;
    svg += `<text x="${padL - 6}" y="${y + 3}" fill="#9aa7b8" font-size="10" `
      + `text-anchor="end">${val.toFixed(0)}ms</text>`;
  }
  for (let t = 0; t < maxTurns; t++) {
    const x = padL + (t / Math.max(1, maxTurns - 1)) * (w - padL - padR);
    svg += `<text x="${x}" y="${h - 10}" fill="#9aa7b8" font-size="10" `
      + `text-anchor="middle">T${t + 1}</text>`;
  }
  series.forEach(s => {
    const pts = s.vals.map((v, t) => v == null ? null : [
      padL + (t / Math.max(1, maxTurns - 1)) * (w - padL - padR),
      padT + (1 - v / maxY) * (h - padT - padB)
    ]).filter(Boolean);
    if (pts.length >= 2) {
      const ptStr = pts.map(p => p.join(",")).join(" ");
      svg += `<polyline fill="none" stroke="${s.color}" stroke-width="2.2" points="${ptStr}"/>`;
    }
    pts.forEach(([cx, cy]) => {
      svg += `<circle cx="${cx}" cy="${cy}" r="3.5" fill="${s.color}"/>`;
    });
  });
  svg += "</svg>";
  el.innerHTML = svg;
}

function renderHeatmap() {
  const wrap = document.getElementById("heatmap-wrap");
  if (!RESULTS.length) { wrap.innerHTML = '<div class="empty">No data</div>'; return; }
  const cats = Object.keys(RESULTS[0].categories);
  let html = "<table><thead><tr><th>Engine / Model</th>";
  cats.forEach(c => { html += `<th class="num">${esc(c)}</th>`; });
  html += "</tr></thead><tbody>";
  RESULTS.forEach(r => {
    html += `<tr><td><strong>${esc(labelOf(r))}</strong></td>`;
    cats.forEach(c => {
      const cat = r.categories[c];
      if (!cat || cat.skipped) {
        html += `<td class="heat-cell" style="color:#9aa7b8">skipped</td>`;
      } else {
        const s = cat.score;
        const bg = s >= 80
          ? "rgba(66,211,146,0.22)"
          : s >= 55 ? "rgba(246,183,60,0.22)" : "rgba(255,107,107,0.22)";
        const fg = s >= 80 ? "#42d392" : s >= 55 ? "#f6b73c" : "#ff6b6b";
        html += `<td class="heat-cell" style="background:${bg};color:${fg}">`
          + `${s.toFixed(0)}% (${cat.passed}/${cat.total})</td>`;
      }
    });
    html += "</tr>";
  });
  html += "</tbody></table>";
  wrap.innerHTML = html;
}

document.getElementById("meta-line").textContent =
  `${RESULTS.length} run(s) loaded — generated offline from sanitized public benchmark JSONs.`;
renderTable();
renderScoresChart();
renderScatterChart();
renderSpeedChart();
renderContextChart();
renderHeatmap();
</script>
</body>
</html>
"""


def render_html_report(
    results: list[PublicBenchmarkResult],
    output_path: Path = DEFAULT_REPORT_HTML,
) -> Path:
    """Write a self-contained offline HTML graphical report for *results*."""
    resolved = output_path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload_json = json.dumps(
        [r.model_dump(mode="json") for r in results],
        ensure_ascii=False,
    ).replace("</", "<\\/")
    html_text = _HTML_TEMPLATE.replace("__IRIS_BENCH_DATA__", payload_json)
    resolved.write_text(html_text, encoding="utf-8", newline="\n")
    return resolved
