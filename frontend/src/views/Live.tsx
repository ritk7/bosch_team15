import { useState } from "react";
import { Activity, Search, ShieldCheck, Square, Network, FlaskConical } from "lucide-react";
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
    <details className="deck-panel">
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

  // services the finished run broke: its target plus any service its title names (compound runs hit two)
  const culprits = result && (result.outcome.lost > 0 || result.outcome.errors > 0 || result.outcome.recovery_s == null)
    ? target.graph.nodes.map((n) => n.id).filter((id) => id === result.experiment.target || new RegExp(`\\b${id}\\b`, "i").test(result.experiment.title))
    : [];
  const guardActive = running || !!cur?.guard?.tripped;
  const th = cur?.guard?.thresholds ?? { error_rate: 0.6, p95_ms: 3000, unhealthy: 2, max_wall_s: 45 };
  const c = cur?.counters;
  const secondsLeft = cur?.guard && cur.guard.armed && !cur.guard.tripped ? cur.guard.seconds_left : null;

  return (
    <div className="live-workspace deck">
      {/* sticky status strip: what is running, where it is, the live counters and the stop control */}
      <section className="deck-strip" aria-label="Experiment status">
        <div className="deck-strip-title">
          <span className={`session-indicator ${running ? "is-running" : ""}`}><i />{running ? state.phase : result ? "done" : "ready"}</span>
          <strong>{cur ? cur.experiment.title : "Pick a service on the map to begin"}</strong>
        </div>
        <ol aria-label="Experiment progress" className="lifecycle">{PHASES.map((p, i) => {
          // only phases this run really entered get a check mark; a refused start never shows Inject as done
          const reached = cur?.phases ? cur.phases.includes(p.id) : cur != null && i < phaseIndex;
          const isCurrent = p.id === state.phase && (p.id !== "done" || reached || !cur?.phases);
          const done = (reached && !isCurrent) || (p.id === "done" && !!result);
          return <li key={p.id} aria-current={isCurrent ? "step" : undefined} className={done ? "complete" : isCurrent ? "current" : ""}><span>{done ? "✓" : i + 1}</span>{p.label}</li>;
        })}</ol>
        <dl className="deck-counters" aria-label="Live counters">
          <div><dt>Sent</dt><dd>{c?.sent ?? 0}</dd></div>
          <div><dt>{cap(unit.stored)}</dt><dd className={c && c.stored ? "is-ok" : ""}>{c?.stored ?? 0}</dd></div>
          <div><dt>{cap(unit.lost)}</dt><dd className={c && c.lost ? "is-crit" : ""}>{c?.lost ?? 0}</dd></div>
          <div><dt>Errors</dt><dd className={c && c.errors ? "is-crit" : ""}>{c?.errors ?? 0}</dd></div>
        </dl>
        {secondsLeft != null && <div className="deck-countdown" aria-live="polite"><strong>{secondsLeft.toFixed(1)}</strong><span>s left</span></div>}
        {running && <button className="emergency-control" type="button" disabled={busyAction === "kill"} onClick={kill}><Square size={14} aria-hidden="true" />{busyAction === "kill" ? "Restoring…" : "Stop & restore"}</button>}
      </section>

      {(error || state.error) && (
        <p role="alert" className="deck-alert">{error ?? state.error}</p>
      )}

      <div className="deck-grid">
        {/* hero: the dependency map, big enough to read from across a room */}
        <section aria-labelledby="map-h" className="deck-panel deck-map">
          <header className="deck-panel-head">
            <h2 id="map-h"><Network size={16} aria-hidden="true" />{target.name === "hawkbit" ? "Bosch hawkBit" : "Voting app"}: dependency map</h2>
            <span className="deck-meta">
              {state.baseline
                ? <>baseline {state.baseline.rps} {unit.plural}/s · {state.baseline.error_pct}% errors · p95 {ms(state.baseline.p95_ms)}</>
                : <>{target.graph.nodes.length} services · {target.graph.edges.filter(e => e.kind === "dataflow").length} data flows</>}
            </span>
          </header>
          <ServiceMap
            graph={target.graph}
            services={state.services}
            selected={selected}
            target={running ? (cur?.experiment.target ?? null) : null}
            flowing={running}
            culprits={culprits}
            disabled={running || busyAction !== null || !connected}
            onSelect={(id) => {
              setSelected(id);
              setPending(null);
              setSearch("");
            }}
          />
        </section>

        {/* the story: prediction before, reveal after, and the guard that keeps it safe */}
        <div className="deck-side">
          <section aria-labelledby="pred-h" aria-live="polite" className="deck-panel prediction-panel">
            <header className="deck-panel-head">
              <h2 id="pred-h"><ShieldCheck size={16} aria-hidden="true" />{result ? "Predicted vs measured" : "Prediction"}</h2>
              {pending && <span className="deck-meta">before anything is touched</span>}
            </header>
            {result && result.outcome.lost > 0 && (
              <div className="deck-loss" role="status">
                <strong>{result.outcome.lost}</strong>
                <span>{result.outcome.lost === 1 ? unit.singular : unit.plural} {unit.lost}<em>every one was acknowledged with HTTP 200</em></span>
              </div>
            )}
            {busyAction === "predict" && <p className="deck-muted">The planner is working out what will happen…</p>}
            {!pending && !cur && busyAction !== "predict" && (
              <p className="deck-muted">Choose a failure below the map. The AI commits to errors, {unit.plural} lost and recovery time first; then you confirm.</p>
            )}
            {pending && !running && (
              <>
                <PredictionCard prediction={pending} unit={unit} />
                <fieldset className="deck-guess">
                  <legend>Your guess: human against AI</legend>
                  <label>Errors <b>{guess.err}%</b>
                    <input type="range" min={0} max={100} step={5} value={guess.err} onChange={(e) => setGuess({ ...guess, err: Number(e.target.value) })} />
                  </label>
                  <label>{cap(unit.plural)} lost
                    <input type="number" min={0} max={500} value={guess.lost} onChange={(e) => setGuess({ ...guess, lost: Math.max(0, Number(e.target.value)) })} />
                  </label>
                </fieldset>
                <div className="deck-actions">
                  <button type="button" className="deck-primary" onClick={confirm} disabled={busyAction !== null || !connected}>Confirm and inject</button>
                  <button type="button" className="deck-ghost" onClick={() => setPending(null)}>Cancel</button>
                </div>
              </>
            )}
            {!pending && cur && !result && (cur.prediction ? <PredictionCard prediction={cur.prediction} unit={unit} /> :
              <p className="deck-muted">This run has no prediction.</p>)}
            {!pending && result && <Reveal result={result} unit={unit} human={guesses[result.run_id]} />}
          </section>

          {guardActive ? <GuardCard guard={cur?.guard ?? null} last={cur?.timeline[cur.timeline.length - 1]} wallS={Math.max(0, cur?.wall_s ?? (cur ? cur.elapsed_s + 1 : 0))}
            restorationFailed={!!state.error && /restoration failed|Restoration incomplete/.test(state.error)}
            phase={state.phase} busy={running} onKill={kill} killing={busyAction === "kill"} /> : (
            <section className="deck-panel deck-guard-idle" aria-label="Guard">
              <ShieldCheck size={16} aria-hidden="true" />
              <span><b>Guard</b> standing by · plain code, not the model</span>
              <span className="deck-meta">trips at {th.error_rate * 100}% errors · p95 {th.p95_ms / 1000} s · {cur?.guard ? `${th.unhealthy} other services down` : "blast radius"} · {th.max_wall_s} s</span>
            </section>
          )}

          <section aria-labelledby="chart-h" className="deck-panel deck-chart">
            <header className="deck-panel-head">
              <h2 id="chart-h"><Activity size={16} aria-hidden="true" />Errors and latency</h2>
              <span className="deck-meta">guard lines dashed · shaded = fault active</span>
            </header>
            {!cur && <p className="deck-muted">Signals appear when you confirm a run.</p>}
            <LiveChart timeline={cur?.timeline ?? []} faultS={cur?.fault_duration_s ?? 10} faultEnd={faultEnd} guard={cur?.guard ?? null} />
          </section>

          <section aria-labelledby="fleet-h" className="deck-panel deck-fleet">
            <header className="deck-panel-head">
              <h2 id="fleet-h">Last 100 {unit.plural}</h2>
              <span className="deck-meta">checked by id in the database</span>
            </header>
            <FleetGrid fleet={cur?.fleet ?? []} unit={unit} />
          </section>
        </div>

        {/* experiment library for the selected service */}
        <section aria-labelledby="choose-h" className="deck-panel deck-library">
          <header className="deck-panel-head">
            <h2 id="choose-h"><FlaskConical size={16} aria-hidden="true" />{node ? `Break ${node.id}` : "Choose a service"}</h2>
            {node && <label className="scenario-search"><Search size={15} aria-hidden="true" /><span className="sr-only">Filter failures for {selected}</span><input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Filter…" /></label>}
          </header>
          {node && node.notes.length > 0 && <p className="deck-muted deck-notes">{node.notes.join(" · ")}</p>}
          <div className="deck-options">
            {node && !options.length && <p className="deck-muted">No matching failures for this service.</p>}
            {options.map((exp) => (
              <button
                key={exp.id}
                type="button"
                disabled={running || busyAction !== null || !connected}
                onClick={() => choose(exp)}
                aria-pressed={pending?.experiment_id === exp.id}
                className="deck-option"
              >
                <span className="deck-option-top"><b>{exp.title}</b><span className="deck-risk">risk {Math.round(exp.risk_hint * 100)}</span></span>
                <span className="deck-option-desc">{exp.description}</span>
              </button>
            ))}
          </div>
          <div data-stage-hide><MapEditor target={target} disabled={running} onChange={onTargetChange} /></div>
        </section>

        {state.results.length > 0 && <div data-stage-hide className="deck-history"><History results={state.results} unit={target.unit} /></div>}
      </div>
    </div>
  );
}

function History({ results, unit }: { results: ExperimentResult[]; unit: Target["unit"] }) {
  return (
    <section aria-labelledby="hist-h" className="deck-panel">
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
