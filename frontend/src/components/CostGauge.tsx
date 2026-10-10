import { useEffect, useState } from "react";
import type { CostSummary, Yearly } from "../lib/api";
import { inr, pct } from "../lib/format";

// The signature element: a log-scale ruler, with the money saved hatched like a drawing.
const EASE = "transition-[left,width] duration-[900ms] ease-[cubic-bezier(.2,.7,.2,1)]";

function logScale(min: number, max: number) {
  // the scale starts one decade below our cost (₹0.01 when it's free) and ends a decade above the big-model cost
  const floor = min > 0 ? Math.max(1e-4, Math.min(0.01, Math.pow(10, Math.floor(Math.log10(min))))) : 0.01;
  const top = Math.pow(10, Math.ceil(Math.log10(Math.max(max * 1.2, 1))));
  const lo = Math.log10(floor);
  const hi = Math.log10(top);
  const x = (v: number) => ((Math.log10(Math.max(v, floor)) - lo) / (hi - lo)) * 100;
  const ticks: number[] = [];
  for (let e = Math.round(lo); e <= Math.round(hi); e++) ticks.push(Math.pow(10, e));
  return { x, ticks };
}

const tickLabel = (v: number) => `₹${v < 1 ? Number(v.toPrecision(1)) : v.toLocaleString("en-IN")}`;

interface Props {
  cost: CostSummary;
  yearly?: Yearly;
  runKey: string;
}

export function CostGauge({ cost, yearly, runKey }: Props) {
  const { x, ticks } = logScale(cost.cost_inr, cost.baseline_inr);
  const target = x(cost.cost_inr);
  const base = x(cost.baseline_inr);
  const [pos, setPos] = useState(base);

  // One orchestrated moment: on each new run the marker travels from the big-model cost down to ours.
  useEffect(() => {
    setPos(base);
    let inner = 0;
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => setPos(target));
    });
    return () => {
      cancelAnimationFrame(outer);
      cancelAnimationFrame(inner);
    };
  }, [runKey, base, target]);

  const oursLeft = target < base;
  return (
    <section aria-labelledby="cost-heading" className="border-b border-rule pb-8">
      <h2 id="cost-heading" className="sr-only">
        Model spend for this run
      </h2>
      <div className="flex flex-wrap items-baseline justify-between gap-x-10 gap-y-3">
        <p className="tabular-nums">
          <span className="wd-wide text-[2.25rem] leading-none font-semibold">{inr(cost.cost_inr)}</span>
          <span className="ml-3 text-graphite">model spend for this run</span>
        </p>
        <p className="max-w-[56ch] text-graphite">
          {inr(cost.baseline_inr)} if every step went to {cost.baseline_model}.{" "}
          <span className="font-semibold text-ink">{pct(cost.savings_pct)} less.</span>
          {yearly && (
            <>
              {" "}
              Over a year of {yearly.runs.toLocaleString("en-IN")} runs: {inr(yearly.cost_inr)} instead of{" "}
              {inr(yearly.baseline_inr)}.
            </>
          )}
        </p>
      </div>

      <div className="relative mt-9 mb-2 h-[4.25rem]" aria-hidden="true">
        <div
          className={`hatch absolute top-[1.6rem] h-2.5 text-graphite/60 ${EASE}`}
          style={{ left: `${Math.min(pos, base)}%`, width: `${Math.abs(base - pos)}%` }}
        />
        <div className="absolute inset-x-0 top-[2.15rem] h-px bg-ink" />
        {ticks.map((t) => (
          <div key={t} className="absolute top-[2.15rem]" style={{ left: `${x(t)}%` }}>
            <div className="h-2 w-px bg-ink" />
            <span className="absolute top-3 -translate-x-1/2 text-xs whitespace-nowrap text-graphite tabular-nums">
              {tickLabel(t)}
            </span>
          </div>
        ))}
        <Pin left={base} label="Large model only" solid={false} side={oursLeft || base > 85 ? "left" : "right"} />
        <Pin left={pos} label="This run" solid side={oursLeft && pos > 15 ? "left" : "right"} animate />
      </div>
    </section>
  );
}

interface PinProps {
  left: number;
  label: string;
  solid: boolean;
  side: "left" | "right";
  animate?: boolean;
}

function Pin({ left, label, solid, side, animate }: PinProps) {
  return (
    <div className={`absolute top-0 ${animate ? EASE : ""}`} style={{ left: `${left}%` }}>
      <span
        className={`absolute top-0 text-sm whitespace-nowrap ${side === "left" ? "right-2" : "left-2"} ${
          solid ? "font-semibold text-cobalt" : "text-graphite"
        }`}
      >
        {label}
      </span>
      <div className={`absolute top-[0.35rem] h-[1.8rem] w-0.5 -translate-x-1/2 ${solid ? "bg-cobalt" : "bg-graphite"}`} />
      <div
        className={`absolute top-[2.15rem] h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 border-2 ${
          solid ? "border-cobalt bg-cobalt" : "border-graphite bg-surface"
        }`}
      />
    </div>
  );
}
