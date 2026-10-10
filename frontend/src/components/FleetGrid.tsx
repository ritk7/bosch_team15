import type { Unit, VoteState } from "../lib/api";

// The last 100 probe units. Each state has its own fill pattern as well as its own colour.
const CELL: Record<VoteState | "empty", string> = {
  ok: "bg-ok",
  queued: "border-2 border-warn",
  error: "bg-crit",
  lost: "hatch border border-crit text-crit",
  pending: "bg-graphite/35",
  empty: "border border-rule",
};

export function FleetGrid({ fleet, unit }: { fleet: VoteState[]; unit: Unit }) {
  const cells: (VoteState | "empty")[] = [...fleet.slice(-100)];
  while (cells.length < 100) cells.push("empty");
  const count = (s: VoteState) => cells.filter((f) => f === s).length;
  const legend: { s: VoteState; label: string }[] = [
    { s: "ok", label: unit.stored },
    { s: "queued", label: "acknowledged, waiting" },
    { s: "error", label: "error or timeout" },
    { s: "lost", label: unit.lost },
    { s: "pending", label: "no answer yet" },
  ];
  return (
    <div className="flex flex-col gap-4">
      <div
        className="fleet-cells"
        role="img"
        aria-label={`Last ${Math.min(100, fleet.length)} ${unit.plural}: ${legend.map((l) => `${count(l.s)} ${l.label}`).join(", ")}`}
      >
        {cells.map((c, i) => (
          <div key={i} title={`${unit.singular} ${i + 1}: ${c}`} aria-hidden="true" className={`aspect-square rounded-[3px] ${CELL[c]} transition-colors duration-300`} />
        ))}
      </div>
      <ul className="fleet-legend text-graphite">
        {legend.map((l) => (
          <li key={l.s} className="flex items-center gap-2">
            <span aria-hidden="true" className={`inline-block h-3 w-3 shrink-0 rounded-[2px] ${CELL[l.s]}`} />
            <span>
              <span className="font-semibold text-ink tabular-nums">{count(l.s)}</span> {l.label}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
