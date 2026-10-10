import type { GuardView, TimelinePoint } from "../lib/api";

// Error rate (left axis) and p95 latency (right axis) against time since the fault, with the guard's limits drawn in.
const W = 520;
const H = 190;
const PAD = { l: 38, r: 46, t: 12, b: 26 };
const P95_MAX = 5000;

interface Props {
  timeline: TimelinePoint[];
  faultS: number;
  faultEnd: number | null; // when the fault actually ended (guard trip or window end), seconds since inject
  guard: GuardView | null;
}

export function LiveChart({ timeline, faultS, faultEnd, guard }: Props) {
  const tMin = Math.min(-1, ...timeline.map((p) => p.t));
  const tMax = Math.max(faultS + 4, ...timeline.map((p) => p.t));
  const x = (t: number) => PAD.l + ((t - tMin) / (tMax - tMin)) * (W - PAD.l - PAD.r);
  const yRate = (r: number) => PAD.t + (1 - r) * (H - PAD.t - PAD.b);
  const yP95 = (ms: number) => yRate(Math.min(ms, P95_MAX) / P95_MAX);
  const line = (f: (p: TimelinePoint) => number) =>
    timeline.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${f(p).toFixed(1)}`).join(" ");
  const errLimit = guard?.thresholds.error_rate ?? 0.6;
  const p95Limit = guard?.thresholds.p95_ms ?? 3000;
  const end = faultEnd ?? (timeline.length ? Math.min(faultS, timeline[timeline.length - 1].t) : 0);
  const ticks: number[] = [];
  for (let t = Math.ceil(tMin / 5) * 5; t <= tMax; t += 5) ticks.push(t);
  const last = timeline[timeline.length - 1];

  return (
    <figure className="m-0">
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img"
        aria-label={last ? `Error rate ${Math.round(last.error_rate * 100)}%, p95 ${Math.round(last.p95_ms)} ms at ${last.t} s` : "No data yet"}>
        {end > 0 && <rect x={x(0)} y={PAD.t} width={Math.max(0, x(end) - x(0))} height={H - PAD.t - PAD.b} className="fill-cobalt-soft" />}
        {[0, 0.5, 1].map((r) => (
          <g key={r}>
            <line x1={PAD.l} x2={W - PAD.r} y1={yRate(r)} y2={yRate(r)} className="stroke-rule" />
            <text x={PAD.l - 6} y={yRate(r) + 4} textAnchor="end" className="fill-graphite text-[10px]">{r * 100}%</text>
            <text x={W - PAD.r + 6} y={yRate(r) + 4} className="fill-graphite text-[10px]">{(r * P95_MAX) / 1000} s</text>
          </g>
        ))}
        {ticks.map((t) => (
          <text key={t} x={x(t)} y={H - 8} textAnchor="middle" className="fill-graphite text-[10px]">{t} s</text>
        ))}
        <line x1={x(0)} x2={x(0)} y1={PAD.t} y2={H - PAD.b} className="stroke-ink" strokeWidth={1} />
        <line x1={PAD.l} x2={W - PAD.r} y1={yRate(errLimit)} y2={yRate(errLimit)} className="stroke-crit" strokeDasharray="5 4" />
        <text x={W - PAD.r - 4} y={yRate(errLimit) - 4} textAnchor="end" className="fill-crit text-[10px]">guard: errors {errLimit * 100}%</text>
        <line x1={PAD.l} x2={W - PAD.r} y1={yP95(p95Limit)} y2={yP95(p95Limit)} className="stroke-graphite" strokeDasharray="2 4" />
        <text x={PAD.l + 4} y={yP95(p95Limit) - 4} className="fill-graphite text-[10px]">guard: p95 {p95Limit / 1000} s</text>
        {timeline.length > 1 && (
          <>
            <path d={line((p) => yP95(p.p95_ms))} className="fill-none stroke-graphite" strokeWidth={1.5} strokeDasharray="1 0" />
            <path d={line((p) => yRate(p.error_rate))} className="fill-none stroke-crit" strokeWidth={2.25} strokeLinejoin="round" />
          </>
        )}
        <text x={x(0) + 4} y={PAD.t + 12} className="fill-ink text-[10px]">fault</text>
      </svg>
      <figcaption className="mt-1 flex flex-wrap gap-x-5 gap-y-1 text-xs text-graphite">
        <span className="flex items-center gap-1.5">
          <svg width="18" height="6" aria-hidden="true"><line x1="0" y1="3" x2="18" y2="3" className="stroke-crit" strokeWidth="2.25" /></svg>
          error rate, last 3 s
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="18" height="6" aria-hidden="true"><line x1="0" y1="3" x2="18" y2="3" className="stroke-graphite" strokeWidth="1.5" /></svg>
          p95 latency
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2.5 w-4 bg-cobalt-soft" />
          fault active
        </span>
      </figcaption>
    </figure>
  );
}
