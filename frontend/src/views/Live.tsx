import { useState } from "react";
import { Activity, ArrowRight, Search, ShieldCheck, Square, Network, FlaskConical } from "lucide-react";
import { FleetGrid } from "../components/FleetGrid";
import { GuardCard } from "../components/GuardCard";
import { LiveChart } from "../components/LiveChart";
import { PredictionCard, Reveal } from "../components/PredictionCard";
import { ServiceMap } from "../components/ServiceMap";
import { api, type Experiment, type ExperimentResult, type Phase, type Prediction, type State, type Target } from "../lib/api";
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

interface Props {
  state: State;
  target: Target;
  catalog: Experiment[];
  onChange: () => void;
  connected: boolean;
  onTargetChange: () => void;
}

function MapEditor({ target, disabled, onChange }: { target: Target; disabled: boolean; onChange: () => void }) {
  const ids = target.graph.nodes.map((n) => n.id);
  const [from, setFrom] = useState(ids[0]);
  const [to, setTo] = useState(ids[1] ?? ids[0]);
  const [msg, setMsg] = useState<string | null>(null);
  const exists = target.graph.edges.some((e) => e.kind === "dataflow" && e.from === from && e.to === to);
  async function edit(action: "add" | "remove") {
    setMsg(null);
    try {
      await api.editEdge(from, to, action);
      onChange();
      setMsg(action === "add" ? `Added ${from} → ${to}` : `Removed ${from} → ${to}`);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <details className="border border-rule bg-surface p-4">
      <summary className="cursor-pointer text-sm font-semibold">Correct the map by hand</summary>
      <p className="mt-1 text-[0.8rem] text-graphite">Add a data flow the tool missed, or remove a wrong one. Saved next to the compose file.</p>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <select value={from} onChange={(e) => setFrom(e.target.value)} className="border border-rule bg-surface px-2 py-1">{ids.map((i) => <option key={i}>{i}</option>)}</select>
        <span aria-hidden="true">→</span>
        <select value={to} onChange={(e) => setTo(e.target.value)} className="border border-rule bg-surface px-2 py-1">{ids.map((i) => <option key={i}>{i}</option>)}</select>
        <button type="button" disabled={disabled || exists || from === to} onClick={() => edit("add")} className="border border-cobalt px-2 py-1 text-cobalt disabled:opacity-40">Add flow</button>
        <button type="button" disabled={disabled || !exists} onClick={() => edit("remove")} className="border border-rule px-2 py-1 text-graphite disabled:opacity-40">Remove flow</button>
      </div>
      {msg && <p className="mt-2 text-[0.8rem] text-graphite" role="status">{msg}</p>}
    </details>
  );
}

export function Live({ state, target, catalog, onChange, connected, onTargetChange }: Props) {
  const [selected, setSelected] = useState<string | null>(() => target.graph.nodes.find(n => n.spof)?.id ?? target.graph.nodes[0]?.id ?? null);
  const [search, setSearch] = useState("");
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
  const options = catalog.filter((e) => e.target === selected && `${e.title} ${e.description} ${e.type}`.toLowerCase().includes(search.toLowerCase()));
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

  const faultEnd = !cur
    ? null
    : cur.fault_ended_s != null
      ? cur.fault_ended_s
      : state.phase === "observe" || state.phase === "inject" || state.phase === "restore"
        ? Math.max(0, cur.elapsed_s)
        : 0; // legacy runs do not contain measured fault-end timing; do not invent it
  const phaseIndex = PHASES.findIndex((p) => p.id === state.phase);

  return (
    <div className="live-workspace">
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div>
          <div className="workspace-eyebrow"><Activity size={14} aria-hidden="true" /> RESILIENCE WORKSPACE / {target.name}</div>
          <h1 className="workspace-title wd-wide font-semibold">Controlled chaos.<span> Measured confidence.</span></h1>
          <p className="mt-1 max-w-[70ch] text-graphite">
            Select a service. Predict the impact. Observe recovery — with every {unit.singular} accounted for.
            {state.runner.kind === "sim" ? " Simulation results, not Docker measurements." : " Live Docker measurements."}
          </p>
        </div>
        <p className="text-sm text-graphite tabular-nums">
          {state.baseline ? (
            <>
              Baseline: <span className="text-ink">{state.baseline.rps} {unit.plural}/s</span>,{" "}
              <span className="text-ink">{state.baseline.error_pct}% errors</span>, p95{" "}
              <span className="text-ink">{ms(state.baseline.p95_ms)}</span>
              {state.baseline.total_services != null && (
                <>, <span className="text-ink">{state.baseline.healthy_services}/{state.baseline.total_services} services healthy</span></>
              )}
            </>
          ) : (
            "Baseline is measured before the first experiment."
          )}
        </p>
      </div>

      <div className="overview-metrics" aria-label="Application overview">
        <div><span>Services running</span><strong>{state.services.filter(s => s.state === "running" && s.health !== "unhealthy").length}<small> / {target.graph.nodes.length}</small></strong><span>Live service state</span></div>
        <div><span>Baseline error rate</span><strong>{state.baseline ? `${state.baseline.error_pct}%` : "—"}</strong><span>{state.baseline ? `${state.baseline.rps} ${unit.plural}/second` : "Measured before the first run"}</span></div>
        <div><span>Baseline p95</span><strong>{state.baseline ? ms(state.baseline.p95_ms) : "—"}</strong><span>Steady-load reference</span></div>
        <div><span>Completed experiments</span><strong>{state.results.length}</strong><span>Prediction → evidence</span></div>
      </div>

      <div className="experiment-bar">
        <span className={`session-indicator ${running ? "is-running" : ""}`}><i />{running ? state.phase : "Ready to explore"}</span>
        <ol aria-label="Experiment progress" className="lifecycle">{PHASES.map((p, i) => {
          // only phases this run really entered get a check mark; a refused start never shows Inject as done
          const reached = cur?.phases ? cur.phases.includes(p.id) : cur != null && i < phaseIndex;
          const isCurrent = p.id === state.phase && (p.id !== "done" || reached || !cur?.phases);
          const done = reached && !isCurrent;
          return <li key={p.id} aria-current={isCurrent ? "step" : undefined} className={isCurrent ? "current" : done ? "complete" : ""}><span>{done ? "✓" : i + 1}</span>{p.label}</li>;
        })}</ol>
        {running && <button className="emergency-control" type="button" disabled={busyAction === "kill"} onClick={kill}><Square size={14} aria-hidden="true" />{busyAction === "kill" ? "Restoring…" : "Stop & restore"}</button>}
      </div>

      {(error || state.error) && (
        <p role="alert" className="mt-5 border-l-2 border-crit pl-3 text-crit">{error ?? state.error}</p>
      )}

      <div className="live-columns mt-6 grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)_minmax(0,4fr)]">
        {/* choose */}
        <div className="space-y-6">
          <section aria-labelledby="map-h" className="dashboard-panel map-panel border border-rule bg-surface p-4">
            <span className="panel-eyebrow"><Network size={14} />01 / Understand</span>
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
              disabled={running || busyAction !== null || !connected}
              onSelect={(id) => {
                setSelected(id);
                setPending(null);
                setSearch("");
              }}
            />
          </section>

          <div data-stage-hide><MapEditor target={target} disabled={running} onChange={onTargetChange} /></div>

          <section aria-labelledby="choose-h" className="dashboard-panel scenario-panel border border-rule bg-surface p-4">
            <span className="panel-eyebrow"><FlaskConical size={14} />Experiment library</span>
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
                <label className="scenario-search"><Search size={16} aria-hidden="true" /><span className="sr-only">Filter failures for {selected}</span><input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Filter failures…" /></label>
                <div className="mt-4 space-y-2">
                  {!options.length && <p className="text-sm text-graphite">No matching failures for this service.</p>}
                  {options.map((exp) => (
                    <button
                      key={exp.id}
                      type="button"
                      disabled={running || busyAction !== null || !connected}
                      onClick={() => choose(exp)}
                      aria-pressed={pending?.experiment_id === exp.id}
                      className={`scenario-option w-full border px-3 py-2.5 text-left hover:border-cobalt disabled:opacity-50 ${
                        pending?.experiment_id === exp.id ? "border-cobalt bg-cobalt-soft" : "border-rule"
                      }`}
                    >
                      <span className="flex items-baseline justify-between gap-3">
                        <span className="font-semibold">{exp.title}</span><ArrowRight size={15} aria-hidden="true" />
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
        <div className="observation-column space-y-6">
          <section aria-labelledby="fleet-h" className="dashboard-panel fleet-panel border border-rule bg-surface p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="fleet-h" className="wd-wide text-base font-semibold">
                {cur ? cur.experiment.title : `Probe ${unit.plural}`}
              </h2>
              <ol className="sr-only" aria-label="Experiment phase">
                {PHASES.map((p, i) => (
                  <li
                    key={p.id}
                    aria-current={p.id === state.phase ? "step" : undefined}
                    className={
                      p.id === state.phase
                        ? "font-semibold text-cobalt underline decoration-2 underline-offset-4"
                        : cur && (cur.phases ? cur.phases.includes(p.id) : i < phaseIndex)
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

          <section aria-labelledby="chart-h" className="dashboard-panel signals-panel border border-rule bg-surface p-4">
            <span className="panel-eyebrow"><Activity size={14} />02 / Observe</span>
            <h2 id="chart-h" className="wd-wide mb-3 text-base font-semibold">Impact & recovery</h2>
            {!cur && <p className="mb-4 text-sm text-graphite">No experiment yet. Signals appear when you start a confirmed run.</p>}
            <LiveChart timeline={cur?.timeline ?? []} faultS={cur?.fault_duration_s ?? 10} faultEnd={faultEnd} guard={cur?.guard ?? null} />
          </section>
        </div>

        {/* predict, guard, reveal */}
        <div className="space-y-6">
          <section aria-labelledby="pred-h" aria-live="polite" className="dashboard-panel prediction-panel border border-rule bg-surface p-4">
            <span className="panel-eyebrow"><ShieldCheck size={14} />03 / Predict before inject</span>
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
                  <button type="button" onClick={confirm} disabled={busyAction !== null || !connected}
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

          <GuardCard guard={cur?.guard ?? null} last={cur?.timeline[cur.timeline.length - 1]} wallS={Math.max(0, cur?.wall_s ?? (cur ? cur.elapsed_s + 1 : 0))}
            restorationFailed={!!state.error && /restoration failed|Restoration incomplete/.test(state.error)}
            phase={state.phase} busy={running} onKill={kill} killing={busyAction === "kill"} />
        </div>
      </div>

      {state.results.length > 0 && <div data-stage-hide><History results={state.results} unit={target.unit} /></div>}
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
                <td className="py-2 pr-3 text-right">{r.outcome.recovery_s == null ? "not observed" : `${r.outcome.recovery_s.toFixed(1)} s`}</td>
                <td className="py-2 pr-3">{r.outcome.guard_tripped ? "tripped" : "not needed"}</td>
                <td className="py-2 text-right text-graphite">
                  {r.prediction_error
                    ? `${r.prediction_error.errors_pct_points} pts, ${r.prediction_error.votes_lost} ${unit.plural}, recovery ${r.outcome.recovery_s != null && r.prediction_error.recovery_s != null ? `${r.prediction_error.recovery_s} s` : 'unavailable'}`
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
