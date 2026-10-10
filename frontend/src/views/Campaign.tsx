import { useState } from "react";
import { api, type Experiment, type State, type Target } from "../lib/api";
import { SOURCE_LABEL } from "../lib/format";

// The planner's picks with reasons, the digital twin rehearsal as a heatmap, and AI vs random computed on the twin.
export function Campaign({ state, target, catalog, onChange }: { state: State; target: Target; catalog: Experiment[]; onChange: () => void }) {
  const [budget, setBudget] = useState(5);
  const [error, setError] = useState<string | null>(null);
  const c = state.campaign;
  const running = state.busy || !["idle", "done"].includes(state.phase);
  const byId = new Map(catalog.map((e) => [e.id, e]));
  const result = (id: string) => [...state.results].reverse().find((r) => r.experiment.id === id);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      onChange();
    }
  }

  const services = target.graph.nodes.map((n) => n.id);
  const types = ["crash", "hang", "network_cut", "cpu_throttle", "traffic_spike", "compound"];
  const rows = state.twin?.rows ?? [];
  const cell = (svc: string, type: string) => rows.find((r) => r.target === svc && r.type === type);
  const maxPen = Math.max(1, ...rows.map((r) => r.penalty));

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="wd-wide text-[1.45rem] leading-tight font-semibold sm:text-[1.75rem]">Campaign</h1>
          <p className="mt-1 max-w-[70ch] text-graphite">
            The planner picks the experiment most likely to reveal a new weakness, says why, predicts it, runs it, and
            adapts to what it measured. The digital twin rehearses the whole catalog first, in seconds.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <label className="flex items-center gap-2 text-graphite">
            Budget
            <input type="number" min={1} max={catalog.length} value={budget} onChange={(e) => setBudget(Number(e.target.value))}
              className="w-16 border border-rule bg-surface px-2 py-1 text-ink tabular-nums" />
          </label>
          <button type="button" disabled={running} onClick={() => act(() => api.twin())}
            className="border border-cobalt px-3 py-2 font-semibold text-cobalt hover:bg-cobalt-soft disabled:opacity-50">
            Rehearse all on the twin
          </button>
          <button type="button" disabled={running} onClick={() => act(() => api.startCampaign(budget, "ai"))}
            className="bg-cobalt px-4 py-2 font-semibold text-on-cobalt hover:opacity-90 disabled:opacity-50">
            Run AI campaign
          </button>
          <button type="button" disabled={running} onClick={() => act(() => api.startCampaign(budget, "random"))}
            className="border border-rule px-3 py-2 text-graphite hover:text-ink disabled:opacity-50">
            Random order
          </button>
          {c.status === "running" && (
            <button type="button" onClick={() => act(() => api.stopCampaign())} className="bg-crit px-3 py-2 font-semibold text-paper">
              Stop and restore
            </button>
          )}
        </div>
      </div>
      {error && <p role="alert" className="border-l-2 border-crit pl-3 text-crit">{error}</p>}

      {state.compare && (
        <section aria-labelledby="cmp-h" className="border border-rule bg-surface p-4">
          <h2 id="cmp-h" className="wd-wide text-base font-semibold">AI planner against random order</h2>
          <div className="mt-3 flex flex-wrap items-baseline gap-x-10 gap-y-2">
            <p><span className="wd-wide text-[2.25rem] leading-none font-semibold text-cobalt tabular-nums">{state.compare.planner_runs_to_top3 ?? "—"}</span>
              <span className="ml-2 text-graphite">runs for the planner</span></p>
            <p><span className="wd-wide text-[2.25rem] leading-none font-semibold tabular-nums">{state.compare.random_runs_to_top3 ?? "—"}</span>
              <span className="ml-2 text-graphite">runs for random order, on average</span></p>
          </div>
          <p className="mt-2 text-sm text-graphite">{state.compare.note}</p>
        </section>
      )}

      <section aria-labelledby="tw-h" className="border border-rule bg-surface p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="tw-h" className="wd-wide text-base font-semibold">Digital twin rehearsal</h2>
          <span className="text-sm text-graphite">
            {state.twin?.status === "running" ? `rehearsing… ${rows.length} done` : state.twin ? `${rows.length} experiments in ${state.twin.seconds} s` : "not run yet"}
          </span>
        </div>
        <p className="mt-1 text-[0.8rem] text-graphite">Predicted damage per service and failure type, from the twin (simulation with the same graph and restart policies). Darker = worse.</p>
        <div className="mt-4 overflow-x-auto">
          <table className="border-collapse text-[0.8rem]">
            <thead>
              <tr>
                <th className="p-1" />
                {types.map((t) => <th key={t} scope="col" className="p-1 text-left font-medium text-graphite">{t.replace("_", " ")}</th>)}
              </tr>
            </thead>
            <tbody>
              {services.map((svc) => (
                <tr key={svc}>
                  <th scope="row" className="wd-wide p-1 pr-3 text-left font-semibold">{svc}</th>
                  {types.map((t) => {
                    const r = cell(svc, t);
                    const level = r ? r.penalty / maxPen : 0;
                    return (
                      <td key={t} className="p-0.5">
                        <div title={r ? `${r.title}: penalty ${r.penalty}, ${r.error_pct}% errors, ${r.lost} lost` : "not in the catalog"}
                          className={`flex h-10 w-24 items-center justify-center text-[0.75rem] tabular-nums ${r ? "border border-rule" : "border border-dashed border-rule"} ${r && r.lost > 0 ? "hatch text-crit" : ""}`}
                          style={r && r.lost === 0 ? { background: `color-mix(in srgb, var(--color-crit) ${Math.round(level * 85)}%, transparent)` } : undefined}>
                          <span className={`${r && r.lost > 0 ? "bg-surface px-1 text-crit" : level > 0.55 ? "text-paper" : "text-ink"} font-semibold`}>
                            {r ? (r.lost > 0 ? `${r.lost} lost` : r.penalty.toFixed(0)) : ""}
                          </span>
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="pk-h">
        <h2 id="pk-h" className="wd-wide text-base font-semibold">
          Picks {c.picks.length > 0 && <span className="text-sm font-normal text-graphite">({c.strategy === "ai" ? "AI planner" : "random"}, {c.status}, {c.picks.length}/{c.budget})</span>}
        </h2>
        {c.picks.length === 0 && <p className="mt-2 text-graphite">No campaign yet.</p>}
        <ol className="mt-3 space-y-3">
          {c.picks.map((p) => {
            const r = result(p.experiment_id);
            return (
              <li key={p.order} className="border border-rule bg-surface p-4">
                <div className="flex flex-wrap items-baseline gap-x-3">
                  <span className="wd-wide text-lg font-semibold text-graphite tabular-nums">{p.order}</span>
                  <span className="font-semibold">{byId.get(p.experiment_id)?.title ?? p.experiment_id}</span>
                  <span className="text-xs text-graphite">{SOURCE_LABEL[p.decided_by] ?? p.decided_by}{p.model && p.decided_by !== "rules" ? ` (${p.model})` : ""}</span>
                </div>
                <p className="mt-1 text-sm"><span className="text-graphite">Why: </span>{p.reason}</p>
                {r && (
                  <p className="mt-1 text-sm tabular-nums text-graphite">
                    Measured: {r.outcome.error_pct}% errors, <span className={r.outcome.lost ? "font-semibold text-crit" : ""}>{r.outcome.lost} {target.unit.plural} {target.unit.lost}</span>,
                    recovery {r.outcome.recovery_s == null ? "not observed" : `${r.outcome.recovery_s} s`}{r.outcome.guard_tripped ? ", guard tripped" : ""}.
                  </p>
                )}
              </li>
            );
          })}
        </ol>
      </section>
    </div>
  );
}
