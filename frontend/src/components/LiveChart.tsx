import type { GuardView, TimelinePoint } from "../lib/api";
import { chartMetrics } from "../lib/chart";

const W = 560;
const H = 300;
const LEFT = 46;
const RIGHT = W - 16;
const PLOT_H = 88;

interface Props {
  timeline: TimelinePoint[];
  faultS: number;
  faultEnd: number | null;
  guard: GuardView | null;
}

export function LiveChart({ timeline, faultS, faultEnd, guard }: Props) {
  const errLimit = guard?.thresholds.error_rate ?? 0.6;
  const p95Limit = guard?.thresholds.p95_ms ?? 3000;
  const { points, peakError, peakLatency, latencyMax } = chartMetrics(timeline, p95Limit);
  const tMin = points.reduce((min, p) => Math.min(min, p.t), -1);
  const tMax = points.reduce((max, p) => Math.max(max, p.t), Math.max(4, faultS + 4));
  const x = (t: number) => LEFT + ((t - tMin) / (tMax - tMin)) * (RIGHT - LEFT);
  const end = Math.min(tMax, Math.max(0, faultEnd ?? points.at(-1)?.t ?? 0));
  const tickStep = Math.max(5, Math.ceil((tMax - tMin) / 6 / 5) * 5);
  const ticks: number[] = [];
  for (let t = Math.ceil(tMin / tickStep) * tickStep; t <= tMax; t += tickStep) ticks.push(t);

  return (
    <figure className="m-0">
      <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-graphite">
        <span>Peak rolling errors <strong className="text-crit">{(peakError * 100).toFixed(1)}%</strong></span>
        <span>Peak p95 <strong className="text-ink">{(peakLatency / 1000).toFixed(2)} s</strong></span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img"
        aria-label={points.length ? `Separate error and latency charts. Peak error rate ${(peakError * 100).toFixed(1)} percent; peak p95 ${(peakLatency / 1000).toFixed(2)} seconds.` : "No measurements yet"}>
        {[
          { top: 30, max: 1, title: "ERROR RATE · rolling 3 s", color: "crit", limit: errLimit,
            value: (p: TimelinePoint) => p.error_rate, label: (v: number) => `${Math.round(v * 100)}%` },
          { top: 178, max: latencyMax, title: "P95 LATENCY · seconds (independent scale)", color: "graphite", limit: p95Limit,
            value: (p: TimelinePoint) => p.p95_ms, label: (v: number) => `${v / 1000} s` },
        ].map(plot => {
          const y = (v: number) => plot.top + PLOT_H * (1 - v / plot.max);
          const path = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(plot.value(p)).toFixed(1)}`).join(" ");
          return <g key={plot.title}>
            <text x={LEFT} y={plot.top - 16} className={`${plot.color === "crit" ? "fill-crit" : "fill-graphite"} text-[10px]`}>{plot.title}</text>
            {points.length > 0 && end > 0 && <rect x={x(0)} y={plot.top} width={x(end) - x(0)} height={PLOT_H} className="fill-cobalt-soft" />}
            {[0, .5, 1].map(r => <g key={r}>
              <line x1={LEFT} x2={RIGHT} y1={y(r * plot.max)} y2={y(r * plot.max)} className="stroke-rule" />
              <text x={LEFT - 7} y={y(r * plot.max) + 4} textAnchor="end" className="fill-graphite text-[10px]">{plot.label(r * plot.max)}</text>
            </g>)}
            {ticks.map(t => <text key={t} x={x(t)} y={plot.top + PLOT_H + 16} textAnchor="middle" className="fill-graphite text-[10px]">{t} s</text>)}
            <line x1={LEFT} x2={RIGHT} y1={y(plot.limit)} y2={y(plot.limit)} className={plot.color === "crit" ? "stroke-crit" : "stroke-graphite"} strokeDasharray="3 5" />
            <text x={RIGHT - 3} y={y(plot.limit) - 5} textAnchor="end" className={`${plot.color === "crit" ? "fill-crit" : "fill-graphite"} text-[10px]`}>guard {plot.label(plot.limit)}</text>
            {points.length > 0 && <>
              <line x1={x(0)} x2={x(0)} y1={plot.top} y2={plot.top + PLOT_H} className="stroke-ink" />
              <path d={path} className={`fill-none ${plot.color === "crit" ? "stroke-crit" : "stroke-graphite"}`} strokeWidth={2} strokeLinejoin="round" />
              {points.length === 1 && <circle cx={x(points[0].t)} cy={y(plot.value(points[0]))} r={2} className={plot.color === "crit" ? "fill-crit" : "fill-graphite"} />}
            </>}
          </g>;
        })}
        {points.length === 0 && <text x={W / 2} y={H / 2} textAnchor="middle" className="fill-graphite text-[12px]">Waiting for measured traffic</text>}
      </svg>
      <figcaption className="mt-2 text-xs text-graphite">
        Shaded region: fault applied until restoration completed. Errors use a fixed 0–100% scale; latency auto-scales in seconds.
      </figcaption>
    </figure>
  );
}
