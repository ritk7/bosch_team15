import type { ExperimentResult, Prediction, Unit } from "../lib/api";
import { SOURCE_LABEL } from "../lib/format";

// Before the run: what the planner commits to. After the run: predicted next to measured, with the difference.
const fmtS = (v: number | null) => (v == null ? "never" : `${v.toFixed(1)} s`);

function signed(v: number, unit = "") {
  if (v === 0) return `0${unit}`;
  return `${v > 0 ? "+" : "−"}${Math.abs(Math.round(v * 10) / 10)}${unit}`;
}

export function PredictionCard({ prediction, unit }: { prediction: Prediction; unit: Unit }) {
  return (
    <div>
      <p className="text-[0.95rem] leading-relaxed">{prediction.summary}</p>
      <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-rule pt-3">
        {[
          ["Errors", `${prediction.expected_error_pct}%`],
          [`${unit.plural[0].toUpperCase()}${unit.plural.slice(1)} lost`, String(prediction.expected_votes_lost)],
          ["Recovery", `${prediction.expected_recovery_s} s`],
        ].map(([k, v]) => (
          <div key={k}>
            <dt className="text-[0.75rem] text-graphite">{k}</dt>
            <dd className="wd-wide m-0 text-[1.5rem] leading-tight font-semibold tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-[0.8rem] text-graphite">
        Hypothesis: {prediction.hypothesis}. Confidence {prediction.confidence}.
        {prediction.recommended_fault_s ? ` Fault window ${prediction.recommended_fault_s} s (riskier experiments get shorter windows).` : ""} Decided by{" "}
        {SOURCE_LABEL[prediction.decided_by] ?? prediction.decided_by}
        {prediction.decided_by !== "rules" ? ` (${prediction.model})` : ""}.
      </p>
    </div>
  );
}

export function Reveal({ result, unit, human }: { result: ExperimentResult; unit: Unit; human?: { err: number; lost: number } }) {
  const o = result.outcome;
  const p = result.prediction;
  const rows: { k: string; pred: string; act: string; diff: string | null; bad: boolean }[] = [
    { k: "Errors", pred: p ? `${p.expected_error_pct}%` : "—", act: `${o.error_pct}%`,
      diff: result.prediction_error ? signed(result.prediction_error.errors_pct_points, " pts") : null, bad: o.errors > 0 },
    { k: `${unit.plural[0].toUpperCase()}${unit.plural.slice(1)} ${unit.lost}`, pred: p ? String(p.expected_votes_lost) : "—",
      act: String(o.lost), diff: result.prediction_error ? signed(result.prediction_error.votes_lost) : null, bad: o.data_loss },
    { k: "Recovery", pred: p ? `${p.expected_recovery_s.toFixed(1)} s` : "—", act: fmtS(o.recovery_s),
      diff: result.prediction_error ? signed(result.prediction_error.recovery_s, " s") : null, bad: o.recovery_s == null },
  ];
  return (
    <div>
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">Predicted against measured for {result.experiment.title}</caption>
        <thead>
          <tr className="border-b border-ink text-left text-[0.75rem] text-graphite">
            <th scope="col" className="py-1.5 pr-2 font-medium" />
            <th scope="col" className="py-1.5 pr-2 text-right font-medium">Predicted</th>
            <th scope="col" className="py-1.5 pr-2 text-right font-medium">Measured</th>
            <th scope="col" className="py-1.5 text-right font-medium">Difference</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {rows.map((r) => (
            <tr key={r.k} className="border-b border-rule">
              <th scope="row" className="py-2 pr-2 text-left font-normal text-graphite">{r.k}</th>
              <td className="py-2 pr-2 text-right">{r.pred}</td>
              <td className={`wd-wide py-2 pr-2 text-right text-[1.05rem] font-semibold ${r.bad ? "text-crit" : ""}`}>{r.act}</td>
              <td className="py-2 text-right text-graphite">{r.diff ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {human && p && (() => {
        const humanOff = Math.abs(human.err - o.error_pct) + Math.abs(human.lost - o.lost) * 2;
        const aiOff = Math.abs(p.expected_error_pct - o.error_pct) + Math.abs(p.expected_votes_lost - o.lost) * 2;
        return (
          <p className="mt-3 border-l-2 border-cobalt pl-3 text-sm">
            Your guess: {human.err}% errors, {human.lost} lost.{" "}
            <span className="font-semibold">{humanOff < aiOff ? "You beat the AI this time." : humanOff === aiOff ? "A draw with the AI." : "The AI was closer."}</span>{" "}
            <span className="text-graphite">(off by {Math.round(humanOff)} against the AI's {Math.round(aiOff)}; lost units count double)</span>
          </p>
        );
      })()}
      <p className="mt-3 text-[0.8rem] text-graphite">
        {o.sent} {o.sent === 1 ? unit.singular : unit.plural} sent, {o.acked} acknowledged, {o.stored} {unit.stored}
        {o.lost ? `, ${o.lost} ${unit.lost}` : ""}. Peak p95 {Math.round(o.peak_p95_ms)} ms.{" "}
        {o.guard_tripped ? `The guard stopped the fault: ${o.guard_reason?.toLowerCase()}.` : "The guard did not need to step in."}
      </p>
      {(result.notes ?? []).map((n) => (
        <p key={n} className="mt-2 border-l-2 border-warn pl-3 text-[0.8rem] text-warn">{n}</p>
      ))}
    </div>
  );
}
