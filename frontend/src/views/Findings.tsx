import { useState } from "react";
import { api, type Severity, type State, type Target } from "../lib/api";
import { SOURCE_LABEL } from "../lib/format";

// Findings ranked by severity with evidence, the resilience score and its breakdown, fixes with an approve step,
// and the before/after proof after a re-run.
const SEV: Record<Severity, string> = {
  critical: "border-crit text-crit",
  high: "border-warn text-warn",
  medium: "border-graphite text-graphite",
  low: "border-rule text-graphite",
};

export function Findings({ state, target, onChange }: { state: State; target: Target; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const running = state.busy || !["idle", "done"].includes(state.phase);
  const a = state.analysis;
  const fx = state.fixes;
  const hist = state.score_history ?? [];
  const before = [...hist].reverse().find((h) => h.label === "before fix");
  const after = [...hist].reverse().find((h) => h.label === "after fix");

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
      onChange();
    }
  }

  const analyze = () => act("analyze", async () => { await api.analyze(); await api.fixes(); });
  const apply = () => act("apply", async () => { await api.applyFixes(chosen); setChosen([]); });
  const rerun = () => act("rerun", () => api.rerun());
  const reset = () => act("reset", () => api.reset());

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="wd-wide text-[1.45rem] leading-tight font-semibold sm:text-[1.75rem]">Findings and fixes</h1>
          <p className="mt-1 max-w-[70ch] text-graphite">
            Weaknesses ranked by severity, each backed by measured numbers from a run. Approve a fix, re-run the same
            experiments, and the score shows whether it worked.
          </p>
        </div>
        <div className="flex flex-wrap gap-3 text-sm">
          <button type="button" onClick={analyze} disabled={running || busy !== null || state.results.length === 0}
            className="bg-cobalt px-4 py-2 font-semibold text-on-cobalt hover:opacity-90 disabled:opacity-50">
            {busy === "analyze" ? "Analysing…" : a ? "Analyse again" : "Analyse runs"}
          </button>
          <button type="button" onClick={reset} disabled={running || busy !== null}
            className="border border-rule px-3 py-2 text-graphite hover:text-ink disabled:opacity-50">
            Reset demo
          </button>
        </div>
      </div>

      {error && <p role="alert" className="mt-5 border-l-2 border-crit pl-3 text-crit">{error}</p>}
      {state.results.length === 0 && (
        <p className="mt-6 border-t border-rule pt-6 text-graphite">No runs yet. Break something on the Live tab first.</p>
      )}

      {a && (
        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          <section aria-labelledby="f-h" className="space-y-4">
            <h2 id="f-h" className="wd-wide text-base font-semibold">
              {a.findings.length} {a.findings.length === 1 ? "weakness" : "weaknesses"} found
              <span className="ml-2 text-sm font-normal text-graphite">
                {a.decided_by === "rules" ? "updated live after every run (rules); press Analyse with AI for the AI write-up" : `written by ${SOURCE_LABEL[a.decided_by] ?? a.decided_by} (${a.model})`}, evidence from measurements
              </span>
            </h2>
            {a.findings.length === 0 && <p className="text-graphite">No experiment caused measurable harm.</p>}
            {a.findings.map((f, i) => (
              <article key={f.id} className="deck-panel">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="wd-wide text-lg font-semibold text-graphite tabular-nums">{i + 1}</span>
                  <span className={`border px-1.5 py-0.5 text-xs font-semibold ${SEV[f.severity]}`}>{f.severity}</span>
                  <span className="text-xs text-graphite">{f.service} · {f.experiment_id} · confidence {f.confidence}</span>
                </div>
                <h3 className="mt-2 font-semibold">{f.title}</h3>
                <p className="mt-1 text-sm">{f.impact}</p>
                <ul className="mt-3 space-y-1 border-t border-rule pt-3 text-[0.8rem] text-graphite">
                  {f.evidence.map((e) => (
                    <li key={e} className="wd-condensed flex gap-2"><span aria-hidden="true">›</span><span>{e}</span></li>
                  ))}
                </ul>
              </article>
            ))}
            {(() => {
              // coverage is computed from the score's per-service ranking: what the score does not yet cover
              const svc = a.score.services ?? [];
              const untested = svc.filter((x) => x.value == null).map((x) => x.service);
              if (!svc.length || !untested.length) return null;
              return (
                <aside className="coverage-note" aria-label="Coverage">
                  <strong>{svc.length - untested.length} of {svc.length} services tested</strong>
                  <span>The score only covers what was broken. Untested: {untested.join(", ")}. Run an experiment on {untested.length === 1 ? "it" : "them"} from the Live tab, or let the Campaign tab pick.</span>
                </aside>
              );
            })()}
          </section>

          <div className="space-y-6">
            <section aria-labelledby="s-h" className="deck-panel">
              <h2 id="s-h" className="wd-wide text-base font-semibold">Resilience score</h2>
              <div className="mt-2 flex items-baseline gap-4">
                <span className={`wd-wide text-[3.25rem] leading-none font-semibold tabular-nums ${a.score.value < 50 ? "text-crit" : a.score.value < 80 ? "text-warn" : "text-ok"}`}>
                  {Math.round(a.score.value)}
                </span>
                <span className="text-graphite">out of 100</span>
              </div>
              {before && after && (
                <p className="mt-3 text-sm" aria-live="polite">
                  Before fix <span className="font-semibold tabular-nums">{Math.round(before.value)}</span> → after fix{" "}
                  <span className="font-semibold tabular-nums text-ok">{Math.round(after.value)}</span>{" "}
                  ({after.value >= before.value ? "+" : ""}{Math.round(after.value - before.value)} points, measured on a re-run)
                </p>
              )}
              {a.score.services && a.score.services.length > 0 && (
                <div className="mt-4">
                  <h3 className="text-[0.8rem] font-semibold">Services ranked by resilience, weakest first</h3>
                  <ul className="mt-2 space-y-1.5">
                    {a.score.services.map((s) => (
                      <li key={s.service} className="grid grid-cols-[5.5rem_minmax(0,1fr)_2.5rem] items-center gap-2 text-[0.8rem]">
                        <span className="wd-wide font-semibold">{s.service}</span>
                        <span className="relative h-2 bg-rule" title={s.worst ? `worst: ${s.worst}` : ""}>
                          {s.value != null && (
                            <span className={`absolute inset-y-0 left-0 ${s.value < 50 ? "bg-crit" : s.value < 80 ? "bg-warn" : "bg-ok"}`} style={{ width: `${s.value}%` }} />
                          )}
                        </span>
                        <span className="text-right tabular-nums">{s.value == null ? "untested" : Math.round(s.value)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <table className="mt-4 w-full border-collapse text-[0.8rem]">
                <tbody className="tabular-nums">
                  {a.score.breakdown.map((b) => (
                    <tr key={b.name} className="border-t border-rule align-top">
                      <td className="py-1.5 pr-2">
                        <span className="text-ink">{b.name}</span>
                        <span className="block text-graphite">{b.note}</span>
                      </td>
                      <td className="py-1.5 text-right font-semibold whitespace-nowrap">{b.points}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-3 text-xs text-graphite">Deterministic formula (backend/app/score.py): the worst weakness counts in full, others at 35%, so harmless experiments never raise the score.</p>
            </section>

            {fx && (
              <section aria-labelledby="x-h" className="deck-panel">
                <h2 id="x-h" className="wd-wide text-base font-semibold">Recommended fixes</h2>
                <p className="mt-1 text-xs text-graphite">The engineer decides. Only compose changes can be applied here; the rest is advice.</p>
                <div className="mt-3 space-y-3">
                  {fx.fixes.map((x) => {
                    const applied = state.applied_fixes.includes(x.id);
                    return (
                      <div key={x.id} className="border border-rule p-3">
                        <label className="flex items-start gap-3">
                          <input type="checkbox" className="mt-1 accent-[var(--color-cobalt)]" disabled={!x.auto_applicable || applied}
                            checked={applied || chosen.includes(x.id)}
                            onChange={(e) => setChosen((c) => (e.target.checked ? [...c, x.id] : c.filter((i) => i !== x.id)))} />
                          <span>
                            <span className="font-semibold">{x.title}</span>
                            <span className="block text-xs text-graphite">
                              {x.change_type} · for {x.finding_id} · est. +{x.expected_score_gain} points
                              {applied ? " · applied" : x.auto_applicable ? "" : " · advice"}
                            </span>
                          </span>
                        </label>
                        <pre className="wd-condensed mt-2 overflow-x-auto bg-paper p-2 text-[0.75rem] leading-snug">{x.snippet}</pre>
                        <ul className="mt-2 list-disc pl-5 text-[0.8rem] text-graphite">
                          {x.steps.map((s) => <li key={s}>{s}</li>)}
                        </ul>
                      </div>
                    );
                  })}
                </div>
                <div className="mt-4 flex flex-wrap gap-3">
                  <button type="button" onClick={apply} disabled={chosen.length === 0 || busy !== null || running}
                    className="bg-cobalt px-4 py-2 text-sm font-semibold text-on-cobalt hover:opacity-90 disabled:opacity-50">
                    {busy === "apply" ? "Applying…" : "Approve and apply"}
                  </button>
                  <button type="button" onClick={rerun} disabled={state.applied_fixes.length === 0 || busy !== null || running}
                    className="border border-cobalt px-4 py-2 text-sm font-semibold text-cobalt hover:bg-cobalt-soft disabled:opacity-50">
                    {running ? "Re-running…" : "Re-run the same experiments"}
                  </button>
                </div>
                {running && <p className="mt-2 text-xs text-graphite">Watch it on the Live tab; the score updates when the re-run ends.</p>}
              </section>
            )}
          </div>
        </div>
      )}
      <p className="mt-8 text-xs text-graphite">Units are {target.unit.plural}: “{target.unit.stored}” means confirmed in the database, “{target.unit.lost}” means acknowledged but never stored.</p>
    </div>
  );
}
