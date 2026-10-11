import { Bot, Cpu, ShieldAlert } from "lucide-react";
import type { State, Target } from "../lib/api";

// The system as a live wiring diagram: every stage shows its real latest output, the stage the engine is in glows,
// current flows along the wires, and the wire into the injector is cut when the guard trips.
type Kind = "ai" | "code" | "safety" | "human";
type Status = "idle" | "active" | "done" | "tripped";

interface Stage {
  id: string;
  n: number;
  kind: Kind;
  name: string;
  output: string;
  status: Status;
}

const KIND_LABEL: Record<Kind, string> = { ai: "AI agent", code: "Plain code", safety: "Safety", human: "Engineer" };

function StageBox({ s }: { s: Stage }) {
  const Icon = s.kind === "ai" ? Bot : s.kind === "safety" ? ShieldAlert : Cpu;
  return (
    <li className={`pipe-stage pipe-${s.kind} is-${s.status}`} aria-current={s.status === "active" ? "step" : undefined}>
      <span className="pipe-kind"><Icon size={14} aria-hidden="true" />{String(s.n).padStart(2, "0")} · {KIND_LABEL[s.kind]}</span>
      <strong>{s.name}</strong>
      <span className="pipe-output">{s.output}</span>
      <span className="pipe-num" aria-hidden="true">{String(s.n).padStart(2, "0")}</span>
      {s.status === "active" && <span className="pipe-live">live</span>}
    </li>
  );
}

function Wire({ state }: { state: "idle" | "flowing" | "cut" }) {
  return <li aria-hidden="true" className={`pipe-wire is-${state}`}><span />{state === "flowing" && <i className="pipe-packet" />}{state === "cut" && <b className="pipe-spark">✕</b>}</li>;
}

// the bend that carries the signal from the end of one row back to the start of the next
function Bend({ on }: { on: boolean }) {
  return <div aria-hidden="true" className={`pipe-bend ${on ? "is-flowing" : ""}`}><span />{on && <i className="pipe-packet" />}</div>;
}

export function Pipeline({ state, target }: { state: State; target: Target }) {
  const cur = state.current;
  const phase = state.phase;
  const running = state.busy || !["idle", "done"].includes(phase);
  const guard = cur?.guard;
  const tripped = !!guard?.tripped;
  const inFault = phase === "inject" || phase === "observe";
  const after = ["restore", "recover", "settle", "done"].includes(phase) && !!cur;
  const lastPick = state.campaign.picks[state.campaign.picks.length - 1];
  const flows = target.graph.edges.filter((e) => e.kind === "dataflow").length;
  const fixes = state.fixes?.fixes ?? [];
  const hist = state.score_history ?? [];
  const p = cur?.prediction;

  const status = (active: boolean, done: boolean): Status => (active ? "active" : done ? "done" : "idle");

  const planRow: Stage[] = [
    { id: "map", n: 1, kind: "code", name: "Mapper", status: "done",
      output: `${target.graph.nodes.length} services, ${flows} data flows` },
    { id: "twin", n: 2, kind: "code", name: "Digital twin",
      status: state.twin?.status === "running" ? "active" : state.twin ? "done" : "idle",
      output: state.twin ? `${state.twin.completed ?? state.twin.rows.length} of ${state.twin.attempted ?? state.twin.rows.length} rehearsed${state.twin.seconds ? ` in ${state.twin.seconds} s` : ""}` : "not rehearsed yet" },
    { id: "plan", n: 3, kind: "ai", name: "Planner", status: status(false, !!cur || !!lastPick || !!state.plan),
      output: cur ? `picked: ${cur.experiment.title}` : state.plan ? `ranked ${state.plan.plan.length} experiments` : "waiting for a choice" },
    { id: "predict", n: 4, kind: "ai", name: "Planner predicts", status: status(phase === "baseline" && !!cur, !!p),
      output: p ? `${p.expected_error_pct}% errors · ${p.expected_votes_lost} lost · ${p.expected_recovery_s} s` : "commits to numbers first" },
    { id: "inject", n: 5, kind: "code", name: "Injector", status: tripped ? "tripped" : status(inFault, after),
      output: cur ? `${cur.experiment.type.replace("_", " ")} on ${cur.experiment.target}` : "allow-listed failures only" },
  ];

  const g = cur?.timeline[cur.timeline.length - 1];
  const loopRow: Stage[] = [
    { id: "guard", n: 6, kind: "safety", name: "Guard", status: tripped ? "tripped" : status(!!guard?.armed, after),
      output: tripped ? guard?.reason ?? "tripped" : guard?.armed ? `armed · errors ${Math.round((g?.error_rate ?? 0) * 100)}% · p95 ${((g?.p95_ms ?? 0) / 1000).toFixed(1)} s` : "plain code, every 0.5 s" },
    { id: "probe", n: 7, kind: "code", name: "Probe", status: status(running, after),
      output: cur ? `${cur.counters.acked} acknowledged · ${cur.counters.stored} ${target.unit.stored} · ${cur.counters.lost} ${target.unit.lost}` : `checks every ${target.unit.singular} by id` },
    { id: "restore", n: 8, kind: "safety", name: "Restore", status: status(phase === "restore" || phase === "recover", phase === "settle" || phase === "done"),
      output: phase === "done" && cur ? "every service back; app answering" : "runs no matter what" },
  ];

  const explainRow: Stage[] = [
    { id: "risk", n: 9, kind: "ai", name: "Risk agent", status: status(false, !!state.analysis),
      output: state.analysis ? `${state.analysis.findings.length} finding${state.analysis.findings.length === 1 ? "" : "s"} · ${state.analysis.decided_by === "rules" ? "rules" : "AI"}` : "ranks weaknesses with evidence" },
    { id: "score", n: 10, kind: "code", name: "Score", status: status(false, !!state.analysis),
      output: state.analysis ? `${Math.round(state.analysis.score.value)} / 100` : "deterministic formula" },
    { id: "fix", n: 11, kind: "ai", name: "Fix agent", status: status(false, fixes.length > 0),
      output: fixes.length ? fixes[0].title : "concrete changes" },
    { id: "approve", n: 12, kind: "human", name: "Approve → re-run", status: status(false, state.applied_fixes.length > 0),
      output: hist.length > 1 ? `score ${Math.round(hist[0].value)} → ${Math.round(hist[hist.length - 1].value)}` : state.applied_fixes.length ? "fix applied" : "engineer decides" },
  ];

  const wire = (a: Stage, b: Stage): "idle" | "flowing" | "cut" =>
    b.status === "tripped" || (a.id === "predict" && tripped) ? "cut" : a.status === "done" && (b.status === "active" || b.status === "done") ? "flowing" : a.status === "active" ? "flowing" : "idle";

  const all = [...planRow, ...loopRow, ...explainRow];
  const row = (stages: Stage[]) => stages.flatMap((s, i) => (i === 0 ? [<StageBox key={s.id} s={s} />] : [<Wire key={`w-${s.id}`} state={wire(stages[i - 1], s)} />, <StageBox key={s.id} s={s} />]));

  return (
    <div className="pipeline">
      <header className="pipe-head">
        <div>
          <h1>Pipeline</h1>
          <p>How the AI decides, and what keeps it safe. Live: the stage the engine is in glows and current flows along the wires; when the guard trips, the wire into the injector is cut.</p>
        </div>
        <ul className="pipe-legend" aria-label="Legend">
          <li><span className="sw pipe-ai" />AI agent (Claude Haiku, rules fallback)</li>
          <li><span className="sw pipe-code" />Plain code</li>
          <li><span className="sw pipe-safety" />Safety</li>
          <li><span className="sw pipe-human" />Engineer</li>
        </ul>
      </header>

      {/* the run's data path in one line, every number measured */}
      <ol className="pipe-ticker" aria-label="This run in numbers">
        <li><span>stages done</span><strong>{all.filter((x) => x.status === "done").length}<small>/ {all.length}</small></strong></li>
        <li><span>{target.unit.plural} sent</span><strong>{cur?.counters.sent ?? 0}</strong></li>
        <li><span>{target.unit.stored}</span><strong className="is-ok">{cur?.counters.stored ?? 0}</strong></li>
        <li><span>{target.unit.lost}</span><strong className={cur?.counters.lost ? "is-crit" : ""}>{cur?.counters.lost ?? 0}</strong></li>
        <li><span>guard</span><strong className={tripped ? "is-crit" : guard?.armed ? "is-warn" : ""}>{tripped ? "tripped" : guard?.armed ? "armed" : "idle"}</strong></li>
        <li><span>score</span><strong>{state.analysis ? Math.round(state.analysis.score.value) : "—"}</strong></li>
      </ol>

      <div className="pipe-board">
        <section aria-labelledby="row1"><h2 id="row1" className="pipe-row-title">Plan and inject</h2><ol className="pipe-row">{row(planRow)}</ol></section>
        <Bend on={planRow[4].status === "done" || planRow[4].status === "active"} />
        <section aria-labelledby="row2"><h2 id="row2" className="pipe-row-title">Watch and keep it safe</h2><ol className="pipe-row">{row(loopRow)}</ol></section>
        <Bend on={loopRow[2].status === "done"} />
        <section aria-labelledby="row3"><h2 id="row3" className="pipe-row-title">Explain, fix and prove</h2><ol className="pipe-row">{row(explainRow)}</ol></section>
      </div>

      <p className="pipe-foot">
        {state.runner.kind === "sim" ? "Simulation: " : "Real Docker: "}
        {running ? `${cur?.experiment.title} is in its ${phase} phase.` : cur ? `last run: ${cur.experiment.title}.` : "start an experiment on the Live tab to watch the wiring come alive."}
      </p>
    </div>
  );
}
