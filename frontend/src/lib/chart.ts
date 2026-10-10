import type { TimelinePoint } from "./api";

/** Preserve measured units: percentages are never normalized to the run's peak. */
export function chartMetrics(timeline: TimelinePoint[], latencyLimit = 3000) {
  const points = timeline.filter(p => Number.isFinite(p.t) && Number.isFinite(p.error_rate)
    && p.error_rate >= 0 && p.error_rate <= 1 && Number.isFinite(p.p95_ms) && p.p95_ms >= 0)
    .slice().sort((a, b) => a.t - b.t);
  const peakError = points.reduce((peak, p) => Math.max(peak, p.error_rate), 0);
  const peakLatency = points.reduce((peak, p) => Math.max(peak, p.p95_ms), 0);
  const required = Math.max(5000, latencyLimit * 1.1, peakLatency * 1.1);
  const magnitude = 10 ** Math.floor(Math.log10(required));
  const latencyMax = [1, 2, 5, 10].map(n => n * magnitude).find(n => n >= required)!;
  return { points, peakError, peakLatency, latencyMax };
}
