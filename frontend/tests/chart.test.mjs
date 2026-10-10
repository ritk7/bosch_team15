import { test } from "node:test";
import assert from "node:assert/strict";
import { chartMetrics } from "../src/lib/chart.ts";

const point = (t, error_rate, p95_ms) => ({ t, error_rate, p95_ms, ok: 0, err: 0, unhealthy: 0 });

test("high latency never becomes 100% errors or gets clipped at 5 seconds", () => {
  const metrics = chartMetrics([point(0, 0, 6738), point(1, 0, 9999.1)]);
  assert.equal(metrics.peakError, 0);
  assert.equal(metrics.peakLatency, 9999.1);
  assert.ok(metrics.latencyMax > metrics.peakLatency);
  assert.equal(metrics.points[1].p95_ms, 9999.1);
});
test("low error rates are preserved rather than normalized to the peak", () => {
  assert.equal(chartMetrics([point(0, .04, 100), point(1, .1, 200)]).peakError, .1);
});
test("genuine 100% errors stay 100%", () => {
  assert.equal(chartMetrics([point(0, 1, 5100)]).peakError, 1);
});
test("empty charts and high guard thresholds use a finite scale", () => {
  const metrics = chartMetrics([], 20000);
  assert.ok(Number.isFinite(metrics.latencyMax) && metrics.latencyMax > 20000);
  assert.equal(metrics.peakError, 0);
});
test("invalid values are excluded and points sorted without mutating the input", () => {
  const input = [point(2, .1, 200), point(1, .05, 100), point(NaN, 0, 2), point(3, Infinity, 4), point(4, 0, -1)];
  assert.deepEqual(chartMetrics(input).points.map(p => p.t), [1, 2]);
  assert.equal(input[0].t, 2);
});
