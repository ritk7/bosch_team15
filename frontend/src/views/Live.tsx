import { useState } from "react";
import { FleetGrid } from "../components/FleetGrid";
import { GuardCard } from "../components/GuardCard";
import { LiveChart } from "../components/LiveChart";
import { PredictionCard, Reveal } from "../components/PredictionCard";
import { ServiceMap } from "../components/ServiceMap";
import { api, type Experiment, type ExperimentResult, type Phase, type Prediction, type State, type Target, type TimelinePoint } from "../lib/api";
import { ms } from "../lib/format";

const PHASES: { id: Phase; label: string }[] = [
  { id: "baseline", label: "Baseline" },
  { id: "inject", label: "Inject" },
  { id: "observe", label: "Observe" },
  { id: "restore", label: "Restore" },
  { id: "recover", label: "Recover" },
  { id: "settle", label: "Settle" },
  { id: "done", label: "Done" },
];

const cap = (s: string) => s[0].toUpperCase() + s.slice(1);

function firstBreach(timeline: TimelinePoint[], errLimit: number, p95Limit: number) {
  return timeline.find((p) => p.t >= 0 && (p.error_rate >= errLimit || p.p95_ms >= p95Limit))?.t ?? null;
}

interface Props {
  state: State;
  target: Target;
  catalog: Experiment[];
  onChange: () => void;
}

export function Live({ state, target, catalog, onChange }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [pending, setPending] = useState<Prediction | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [guess, setGuess] = useState<{ err: number; lost: number }>({ err: 50, lost: 0 });
  const [guesses, setGuesses] = useState<Record<string, { err: number; lost: number }>>({});

  const cur = state.current;
  const running = state.busy || !["idle", "done"].includes(state.phase);
  const unit = target.unit;
  const result: ExperimentResult | undefined =
    state.phase === "done" && cur ? state.results.find((r) => r.run_id === cur.run_id) : undefined;
  const options = catalog.filter((e) => e.target === selected);
  const node = target.graph.nodes.find((n) => n.id === selected);

  async function act<T>(name: string, fn: () => Promise<T>): Promise<T | undefined> {
    setBusyAction(name);
    setError(null);
    try {
      return await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return undefined;
    } finally {
      setBusyAction(null);
      onChange();
    }
  }

  const choose = (exp: Experiment) => act("predict", async () => setPending(await api.predict(exp.id)));
  const confirm = () =>
    pending &&
    act("run", async () => {
      const { run_id } = await api.run(pending.experiment_id);
      setGuesses((g) => ({ ...g, [run_id]: guess }));
      setPending(null);
    });
  const kill = () => act("kill", () => api.killswitch());

  const th = cur?.guard.thresholds;
  const faultEnd = !cur
    ? null
    : cur.guard.tripped && th
      ? (firstBreach(cur.timeline, th.error_rate, th.p95_ms) ?? cur.elapsed_s)
      : state.phase === "observe" || state.phase === "inject"
        ? Math.max(0, cur.elapsed_s)
        : PHASES.findIndex((p) => p.id === state.phase) > 2
          ? cur.fault_duration_s
          : 0;
  const phaseIndex = PHASES.findIndex((p) => p.id === state.phase);

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div>
          <h1 className="wd-wide text-[1.45rem] leading-tight font-semibold sm:text-[1.75rem]">Break one service on purpose</h1>
          <p className="mt-1 max-w-[70ch] text-graphite">
            Pick a service on the map and a failure. The planner commits to a prediction first, the guard keeps the damage
            inside limits, and every number below is measured from real probe {unit.plural}.
          </p>
        </div>
        <p className="text-sm text-graphite tabular-nums">
          {state.baseline ? (
            <>
              Baseline: <span className="text-ink">{state.baseline.rps} {unit.plural}/s</span>,{" "}
              <span className="text-ink">{state.baseline.error_pct}% errors</span>, p95{" "}
              <span className="text-ink">{ms(state.baseline.p95_ms)}</span>
            </>
          ) : (
            "Baseline is measured before the first experiment."
          )}
        </p>
      </div>

      {(error || state.error) && (
        <p role="alert" className="mt-5 border-l-2 border-crit pl-3 text-crit">{error ?? state.error}</p>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)_minmax(0,4fr)]">
        {/* choose */}
        <div className="space-y-6">
          <section aria-labelledby="map-h" className="border border-rule bg-surface p-4">
            <h2 id="map-h" className="wd-wide text-base font-semibold">Dependency map: {target.name === "hawkbit" ? "Bosch hawkBit" : "voting app"}</h2>
            <p className="mt-1 mb-3 text-[0.8rem] text-graphite">
              {target.graph.nodes.length} services read from <span className="wd-condensed">{target.compose_file}</span>, plus the data flows the compose file does not declare.
            </p>
            <ServiceMap
              graph={target.graph}
              services={state.services}
              selected={selected}
              target={running ? (cur?.experiment.target ?? null) : null}
              flowing={running || (cur?.counters.sent ?? 0) > 0}
              disabled={running}
              onSelect={(id) => {
                setSelected(id);
                setPending(null);
              }}
            />
          </section>

          <section aria-labelledby="choose-h" className="border border-rule bg-surface p-4">
            <h2 id="choose-h" className="wd-wide text-base font-semibold">
              {node ? `Break ${node.id}` : "Choose a failure"}
            </h2>
            {!node && <p className="mt-1 text-sm text-graphite">Click a service on the map, or focus it and press Enter.</p>}
            {node && (
              <>
                <ul className="mt-2 space-y-1 text-[0.8rem] text-graphite">
                  {node.notes.map((n) => (
                    <li key={n} className="flex gap-2"><span aria-hidden="true">–</span><span>{n}</span></li>
                  ))}
                </ul>
                <div className="mt-4 space-y-2">
                  {options.map((exp) => (
                    <button
                      key={exp.id}
                      type="button"
                      disabled={running || busyAction !== null}
                      onClick={() => choose(exp)}
                      aria-pressed={pending?.experiment_id === exp.id}
                      className={`w-full border px-3 py-2.5 text-left hover:border-cobalt disabled:opacity-50 ${
                        pending?.experiment_id === exp.id ? "border-cobalt bg-cobalt-soft" : "border-rule"
                      }`}
                    >
                      <span className="flex items-baseline justify-between gap-3">
                        <span className="font-semibold">{exp.title}</span>
                        <span className="text-xs text-graphite tabular-nums">risk {Math.round(exp.risk_hint * 100)}</span>
                      </span>
                      <span className="mt-0.5 block text-[0.8rem] text-graphite">{exp.description}</span>
                    </button>
                  ))}
                </div>
              </>
            )}
          </section>
        </div>

        {/* watch */}
        <div className="space-y-6">
          <section aria-labelledby="fleet-h" className="border border-rule bg-surface p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="fleet-h" className="wd-wide text-base font-semibold">
                {cur ? cur.experiment.title : `Probe ${unit.plural}`}
              </h2>
              <ol className="flex flex-wrap gap-x-2.5 text-[0.75rem]" aria-label="Experiment phase">
                {PHASES.map((p, i) => (
                  <li
                    key={p.id}
                    aria-current={p.id === state.phase ? "step" : undefined}
                    className={
                      p.id === state.phase
                        ? "font-semibold text-cobalt underline decoration-2 underline-offset-4"
                        : cur && i < phaseIndex
                          ? "text-ink"
                          : "text-graphite"
                    }
                  >
                    {p.label}
                  </li>
                ))}
              </ol>
            </div>
            <dl className="mt-4 grid grid-cols-5 gap-2 border-b border-rule pb-4">
              {[
                ["Sent", cur?.counters.sent ?? 0, ""],
                ["Acknowledged", cur?.counters.acked ?? 0, ""],
                [cap(unit.stored), cur?.counters.stored ?? 0, ""],
                [cap(unit.lost), cur?.counters.lost ?? 0, "text-crit"],
                ["Errors", cur?.counters.errors ?? 0, "text-crit"],
              ].map(([k, v, c]) => (
                <div key={k as string} className="min-w-0">
                  <dt className="truncate text-[0.72rem] text-graphite">{k}</dt>
                  <dd className={`wd-wide m-0 text-[1.35rem] leading-tight font-semibold tabular-nums ${(v as number) > 0 ? c : ""}`}>{v}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-4">
              <FleetGrid fleet={cur?.fleet ?? []} unit={unit} />
            </div>
          </section>

          <section aria-labelledby="chart-h" className="border border-rule bg-surface p-4">
            <h2 id="chart-h" className="wd-wide mb-3 text-base font-semibold">Errors and latency</h2>
            <LiveChart timeline={cur?.timeline ?? []} faultS={cur?.fault_duration_s ?? 10} faultEnd={faultEnd} guard={cur?.guard ?? null} />
          </section>
        </div>

        {/* predict, guard, reveal */}
        <div className="space-y-6">
          <section aria-labelledby="pred-h" aria-live="polite" className="border border-rule bg-surface p-4">
            <h2 id="pred-h" className="wd-wide mb-2 text-base font-semibold">
              {result ? "Predicted against measured" : "Prediction"}
            </h2>
            {busyAction === "predict" && <p className="text-sm text-graphite">The planner is working out what will happen…</p>}
            {!pending && !cur && busyAction !== "predict" && (
              <p className="text-sm text-graphite">The planner states its expected errors, {unit.plural} lost and recovery time before anything is touched.</p>
            )}
            {pending && !running && (
              <>
                <PredictionCard prediction={pending} unit={unit} />
                <fieldset className="mt-4 border-t border-rule pt-3">
                  <legend className="text-[0.8rem] font-semibold">Your guess (optional): human against AI</legend>
                  <div className="mt-2 grid grid-cols-2 gap-3 text-[0.8rem]">
                    <label className="text-graphite">Errors <span className="font-semibold text-ink tabular-nums">{guess.err}%</span>
                      <input type="range" min={0} max={100} step={5} value={guess.err} onChange={(e) => setGuess({ ...guess, err: Number(e.target.value) })} className="mt-1 w-full accent-[var(--color-cobalt)]" />
                    </label>
                    <label className="text-graphite">{cap(unit.plural)} lost
                      <input type="number" min={0} max={500} value={guess.lost} onChange={(e) => setGuess({ ...guess, lost: Math.max(0, Number(e.target.value)) })} className="mt-1 w-full border border-rule bg-surface px-2 py-1 text-ink tabular-nums" />
                    </label>
                  </div>
                </fieldset>
                <div className="mt-4 flex gap-3">
                  <button type="button" onClick={confirm} disabled={busyAction !== null}
                    className="flex-1 bg-cobalt px-4 py-2.5 font-semibold text-on-cobalt hover:opacity-90 disabled:opacity-60">
                    Confirm and inject
                  </button>
                  <button type="button" onClick={() => setPending(null)} className="px-3 text-graphite hover:text-ink">Cancel</button>
                </div>
              </>
            )}
            {!pending && cur && !result && (cur.prediction ? <PredictionCard prediction={cur.prediction} unit={unit} /> :
              <p className="text-sm text-graphite">This run has no prediction.</p>)}
            {!pending && result && <Reveal result={result} unit={unit} human={guesses[result.run_id]} />}
          </section>

          <GuardCard guard={cur?.guard ?? null} last={cur?.timeline[cur.timeline.length - 1]} wallS={Math.max(0, cur?.elapsed_s ?? 0)}
            phase={state.phase} busy={running} onKill={kill} killing={busyAction === "kill"} />
        </div>
      </div>

      {state.results.length > 0 && <History results={state.results} unit={target.unit} />}
    </div>
  );
}

function History({ results, unit }: { results: ExperimentResult[]; unit: Target["unit"] }) {
  return (
    <section aria-labelledby="hist-h" className="mt-8">
      <h2 id="hist-h" className="wd-wide text-base font-semibold">Runs so far</h2>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-ink text-left text-[0.8rem] text-graphite">
              <th scope="col" className="py-2 pr-3 font-medium">Experiment</th>
              <th scope="col" className="py-2 pr-3 font-medium">Label</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">Errors</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">{cap(unit.plural)} {unit.lost}</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">Recovery</th>
              <th scope="col" className="py-2 pr-3 font-medium">Guard</th>
              <th scope="col" className="py-2 text-right font-medium">Prediction off by</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {[...results].reverse().map((r) => (
              <tr key={r.run_id} className="border-b border-rule">
                <td className="py-2 pr-3">{r.experiment.title}</td>
                <td className="py-2 pr-3 text-graphite">{r.label}</td>
                <td className="py-2 pr-3 text-right">{r.outcome.error_pct}%</td>
                <td className={`py-2 pr-3 text-right ${r.outcome.data_loss ? "font-semibold text-crit" : ""}`}>{r.outcome.lost}</td>
                <td className="py-2 pr-3 text-right">{r.outcome.recovery_s == null ? "never" : `${r.outcome.recovery_s.toFixed(1)} s`}</td>
                <td className="py-2 pr-3">{r.outcome.guard_tripped ? "tripped" : "not needed"}</td>
                <td className="py-2 text-right text-graphite">
                  {r.prediction_error
                    ? `${r.prediction_error.errors_pct_points} pts, ${r.prediction_error.votes_lost} ${unit.plural}, ${r.prediction_error.recovery_s} s`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
