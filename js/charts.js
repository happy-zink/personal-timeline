/* Lightweight SVG chart helpers — no external dependencies.
   Missing values are drawn as gaps (never connected through zero). */
(function (global) {
  const COLORS = [
    "#1f7a68", "#c45c26", "#3d5a80", "#9b5de5", "#2a9d8f",
    "#e76f51", "#264653", "#f4a261", "#6d597a", "#3a7d44"
  ];

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  function niceTicks(min, max, count) {
    if (!isFinite(min) || !isFinite(max)) return [0, 1];
    if (min === max) {
      const pad = Math.abs(min) * 0.1 || 1;
      min -= pad; max += pad;
    }
    const span = max - min;
    const step = Math.pow(10, Math.floor(Math.log10(span / Math.max(1, count - 1))));
    const err = (span / Math.max(1, count - 1)) / step;
    const mult = err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
    const s = step * mult;
    const start = Math.floor(min / s) * s;
    const ticks = [];
    for (let v = start; v <= max + s * 0.5; v += s) ticks.push(v);
    return ticks;
  }

  function lineChart(container, config) {
    const {
      series = [], // [{name, color, points:[{date, value}]}]
      height = 280,
      unitLabel = "",
      showGaps = true,
      title = ""
    } = config;

    const width = Math.max(520, container.clientWidth || 720);
    const pad = { top: 18, right: 18, bottom: 42, left: 48 };
    const innerW = width - pad.left - pad.right;
    const innerH = height - pad.top - pad.bottom;

    // collect numeric domain
    let min = Infinity, max = -Infinity;
    series.forEach(s => {
      s.points.forEach(p => {
        if (p.value == null || p.value === undefined || Number.isNaN(p.value)) return;
        const v = Number(p.value);
        if (v < min) min = v;
        if (v > max) max = v;
      });
    });
    if (!isFinite(min)) { min = 0; max = 1; }
    const ticks = niceTicks(min, max, 5);
    min = ticks[0];
    max = ticks[ticks.length - 1];

    const dateLabels = (series[0] && series[0].points.map(p => p.date)) || [];
    const n = dateLabels.length || 1;
    const xAt = (i) => pad.left + (n === 1 ? innerW / 2 : (i / (n - 1)) * innerW);
    const yAt = (v) => pad.top + innerH - ((v - min) / (max - min || 1)) * innerH;

    let svg = `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" role="img">`;
    if (title) {
      svg += `<text x="${pad.left}" y="14" font-size="12" fill="#6d746f">${esc(title)}</text>`;
    }
    // grid + y ticks
    ticks.forEach(t => {
      const y = yAt(t);
      svg += `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#e8e2d8" stroke-width="1"/>`;
      svg += `<text x="${pad.left - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="#6d746f">${t}</text>`;
    });

    // x labels
    const labelEvery = Math.max(1, Math.floor(n / 8));
    dateLabels.forEach((d, i) => {
      if (i % labelEvery !== 0 && i !== n - 1) return;
      svg += `<text x="${xAt(i)}" y="${height - 12}" text-anchor="middle" font-size="11" fill="#6d746f">${esc(d.slice(5))}</text>`;
    });

    // series paths with gaps
    series.forEach((s, si) => {
      const color = s.color || COLORS[si % COLORS.length];
      let d = "";
      let penDown = false;
      s.points.forEach((p, i) => {
        const missing = p.value == null || p.value === undefined || Number.isNaN(p.value);
        if (missing) {
          penDown = false;
          return;
        }
        const x = xAt(i), y = yAt(Number(p.value));
        d += (penDown ? ` L ${x.toFixed(1)} ${y.toFixed(1)}` : ` M ${x.toFixed(1)} ${y.toFixed(1)}`);
        penDown = true;
      });
      if (d) {
        svg += `<path d="${d.trim()}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>`;
      }
      // points
      s.points.forEach((p, i) => {
        if (p.value == null || p.value === undefined || Number.isNaN(p.value)) return;
        const x = xAt(i), y = yAt(Number(p.value));
        svg += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="${color}"><title>${esc(p.date)}: ${p.value}${unitLabel ? " " + unitLabel : ""}</title></circle>`;
      });
    });

    // axis
    svg += `<line x1="${pad.left}" y1="${pad.top + innerH}" x2="${width - pad.right}" y2="${pad.top + innerH}" stroke="#cfc6b8"/>`;
    svg += `<line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top + innerH}" stroke="#cfc6b8"/>`;
    if (unitLabel) {
      svg += `<text x="12" y="${pad.top + 12}" font-size="11" fill="#6d746f">${esc(unitLabel)}</text>`;
    }
    svg += `</svg>`;
    container.innerHTML = svg;
  }

  function barChart(container, config) {
    const { labels = [], values = [], height = 220, color = "#1f7a68", unitLabel = "" } = config;
    const width = Math.max(420, container.clientWidth || 560);
    const pad = { top: 16, right: 16, bottom: 40, left: 44 };
    const innerW = width - pad.left - pad.right;
    const innerH = height - pad.top - pad.bottom;
    let min = 0, max = 0;
    values.forEach(v => {
      if (v == null) return;
      if (v > max) max = v;
      if (v < min) min = v;
    });
    if (max === min) max = min + 1;
    const ticks = niceTicks(min, max, 4);
    min = ticks[0]; max = ticks[ticks.length - 1];
    const n = labels.length || 1;
    const bw = Math.min(42, (innerW / n) * 0.62);
    const yAt = (v) => pad.top + innerH - ((v - min) / (max - min || 1)) * innerH;

    let svg = `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">`;
    ticks.forEach(t => {
      const y = yAt(t);
      svg += `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#e8e2d8"/>`;
      svg += `<text x="${pad.left - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="#6d746f">${t}</text>`;
    });
    labels.forEach((lab, i) => {
      const x = pad.left + ((i + 0.5) / n) * innerW;
      const v = values[i];
      if (v == null) {
        svg += `<rect x="${x - bw/2}" y="${pad.top + innerH - 3}" width="${bw}" height="3" fill="#d8d0c4"/>`;
        svg += `<text x="${x}" y="${pad.top + innerH + 16}" text-anchor="middle" font-size="10" fill="#a89b8c">缺失</text>`;
      } else {
        const y = yAt(v);
        const h = Math.max(1, pad.top + innerH - y);
        svg += `<rect x="${x - bw/2}" y="${y}" width="${bw}" height="${h}" rx="4" fill="${color}" opacity="0.88"><title>${esc(lab)}: ${v}${unitLabel}</title></rect>`;
      }
      svg += `<text x="${x}" y="${height - 10}" text-anchor="middle" font-size="10" fill="#6d746f">${esc(lab)}</text>`;
    });
    svg += `<line x1="${pad.left}" y1="${pad.top + innerH}" x2="${width - pad.right}" y2="${pad.top + innerH}" stroke="#cfc6b8"/>`;
    svg += `</svg>`;
    container.innerHTML = svg;
  }

  global.TimelineCharts = { lineChart, barChart, COLORS };
})(window);
