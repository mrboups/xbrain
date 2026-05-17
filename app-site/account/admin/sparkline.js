/**
 * sparkline.js — minimal inline-SVG sparkline component.
 *
 * Phase 11 plan 11-11 Task 4. No external dependencies — Chart.js / D3
 * were ruled out by CONTEXT.md (chart-library lock). Renders a polyline
 * with optional per-point circles that carry a <title> for hover tooltips
 * (date + count), plus an aria-label summarizing total + peak for screen
 * readers.
 *
 * Usage:
 *   renderSparkline(containerEl, dailyData, opts)
 *
 *   containerEl: HTMLElement that becomes the parent of the <svg>.
 *   dailyData:   [{date: 'YYYY-MM-DD', events: int}, ...] — always
 *                exactly N entries (the admin API zero-fills missing days).
 *   opts:        { width?: 200, height?: 40, color?: 'currentColor' }
 */

function escapeAttr(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function renderSparkline(container, dailyData, opts) {
  if (!container) return;
  opts = opts || {};
  const width = opts.width || 200;
  const height = opts.height || 40;
  const color = opts.color || "currentColor";
  const data = Array.isArray(dailyData) ? dailyData : [];

  if (data.length === 0) {
    container.innerHTML = `<span class="empty">no data</span>`;
    return;
  }

  // max=1 floor avoids divide-by-zero when every day is 0.
  const max = Math.max(1, ...data.map((d) => Number(d.events) || 0));
  const min = 0;
  const padding = 2;
  const stepX =
    data.length > 1 ? (width - 2 * padding) / (data.length - 1) : 0;

  const coords = data.map((d, i) => {
    const events = Number(d.events) || 0;
    const x = padding + i * stepX;
    const y =
      height -
      padding -
      ((events - min) / (max - min || 1)) * (height - 2 * padding);
    return { x, y, events, date: d.date };
  });

  const polyline = coords
    .map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`)
    .join(" ");

  // Per-point circles carry the tooltip via <title>; screen readers can
  // navigate them with the aria-label on the parent <svg>.
  const circles = coords
    .map(
      (c) =>
        `<circle cx="${c.x.toFixed(1)}" cy="${c.y.toFixed(1)}" r="2" fill="${escapeAttr(
          color,
        )}" opacity="0.6"><title>${escapeAttr(c.date)}: ${c.events}</title></circle>`,
    )
    .join("");

  const total = coords.reduce((a, b) => a + b.events, 0);
  const peakPoint = coords.reduce(
    (best, c) => (c.events > best.events ? c : best),
    coords[0],
  );
  const ariaLabel = `Activity sparkline: ${total} events over ${data.length} days, peak ${peakPoint.events} on ${peakPoint.date}`;

  container.innerHTML = `<svg width="${width}" height="${height}" role="img" aria-label="${escapeAttr(
    ariaLabel,
  )}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
    <polyline points="${polyline}" fill="none" stroke="${escapeAttr(color)}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" />
    ${circles}
  </svg>`;
}
