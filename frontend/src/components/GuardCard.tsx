import type { GuardView, TimelinePoint } from "../lib/api";

// The kill switch's live readout: each threshold as a meter against its limit, plus the countdown of the fault window.
interface Props {
  guard: GuardView | null;
  last: TimelinePoint | undefined;
  wallS: number;
  phase: string;
  busy: boolean;
  onKill: () => void;
  killing: boolean;
  restorationFailed?: boolean;
}

function Meter({ label, value, limit, text }: { label: string; value: number; limit: number; text: string }) {
  const ratio = Math.min(1, limit ? value / limit : 0);
  const breached = value >= limit;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-[0.8rem]">
        <span className="text-graphite">{label}</span>
        <span className={`tabular-nums ${breached ? "font-semibold text-crit" : "text-ink"}`}>{text}</span>
      </div>
      <div className="relative mt-1 h-1.5 bg-rule" aria-hidden="true">
        <div className={`absolute inset-y-0 left-0 ${breached ? "bg-crit" : "bg-ink"} transition-[width] duration-300`}
          style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  );
}

export function GuardCard({ guard, last, wallS, phase, busy, onKill, killing, restorationFailed = false }: Props) {
  const th = guard?.thresholds ?? { error_rate: 0.6, p95_ms: 3000, unhealthy: 3, max_wall_s: 45 };
  const status = restorationFailed ? "Restore failed" : guard?.tripped ? "Tripped" : guard?.armed ? "Armed" : busy ? "Standing by" : "Idle";
  const counting = phase === "observe" && guard?.armed && !guard.tripped;
  return (
    <section aria-labelledby="guard-h" className="guard-card border border-rule bg-surface p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="guard-h" className="wd-wide text-base font-semibold">Guard</h2>
        <span className={`text-sm font-semibold ${guard?.tripped ? "text-crit" : guard?.armed ? "text-cobalt" : "text-graphite"}`}>
          {status}
        </span>
      </div>
      <p className="mt-1 text-[0.8rem] text-graphite">Plain code, not the model. A breached limit triggers restoration. Verify the result on the service map.</p>

      <div className="mt-3 flex min-h-[2rem] items-baseline gap-2" aria-live="polite">
        {counting ? (
          <>
            <span className="wd-wide text-[2rem] leading-none font-semibold tabular-nums">{guard!.seconds_left.toFixed(1)}</span>
            <span className="text-sm text-graphite">s left in the fault window</span>
          </>
        ) : (
          <span className={`text-sm ${restorationFailed ? "text-crit" : "text-graphite"}`}>
            {restorationFailed ? "Restoration incomplete. Retry the kill switch." : phase === "restore" ? "Restoration in progress…" : phase === "inject" ? "Applying controlled fault…" : phase === "observe" && guard?.tripped ? "Safety limit reached; restoration pending." : "No fault active."}
          </span>
        )}
      </div>

      {guard?.tripped && guard.reason && (
        <p role="status" className="mt-3 border-l-2 border-crit pl-3 text-sm text-crit">{guard.reason}. {restorationFailed ? "Restoration failed; inspect service health and retry." : phase === "done" ? "Restore cycle complete; check service health." : "Check restoration progress on the map."}</p>
      )}

      <div className="mt-4 space-y-3">
        <Meter label="Error rate, last 3 s" value={last?.error_rate ?? 0} limit={th.error_rate}
          text={`${Math.round((last?.error_rate ?? 0) * 100)}% / ${th.error_rate * 100}%`} />
        <Meter label="p95 latency" value={last?.p95_ms ?? 0} limit={th.p95_ms}
          text={`${((last?.p95_ms ?? 0) / 1000).toFixed(2)} s / ${th.p95_ms / 1000} s`} />
        <Meter label="Services down or unhealthy" value={last?.unhealthy ?? 0} limit={th.unhealthy}
          text={`${last?.unhealthy ?? 0} / ${th.unhealthy}`} />
        <Meter label="Experiment time" value={wallS} limit={th.max_wall_s} text={`${Math.round(wallS)} s / ${th.max_wall_s} s`} />
      </div>

      <button
        type="button"
        onClick={onKill}
        disabled={killing}
        className="mt-5 w-full bg-crit px-4 py-3 text-left text-paper hover:opacity-90 disabled:opacity-60"
      >
        <span className="wd-wide block text-lg font-semibold">{killing ? "Restoring…" : "Kill switch"}</span>
        <span className="block text-sm opacity-90">Stop the fault now and restore every service</span>
      </button>
    </section>
  );
}
