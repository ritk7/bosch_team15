import { useCallback, useEffect, useState } from "react";
import { api, type CostSummary, type LedgerEntry } from "../lib/api";
import { inr, ms, pct, SOURCE_LABEL } from "../lib/format";

const time = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function LedgerView() {
  const [data, setData] = useState<{ summary: CostSummary; entries: LedgerEntry[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .ledger()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(load, [load]);

  async function clear() {
    await api.resetLedger().catch(() => undefined);
    load();
  }

  const s = data?.summary;
  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="wd-wide text-[1.45rem] leading-tight sm:text-[1.75rem] font-semibold">Cost ledger</h1>
        <div className="flex gap-4 text-sm">
          <button type="button" onClick={load} className="text-cobalt underline-offset-2 hover:underline">
            Refresh
          </button>
          <button type="button" onClick={clear} className="text-graphite underline-offset-2 hover:underline">
            Clear ledger
          </button>
        </div>
      </div>

      {error && (
        <p role="alert" className="mt-6 border-l-2 border-crit pl-3 text-crit">
          {error}
        </p>
      )}

      {s && (
        <p className="mt-4 max-w-[78ch] text-[1.05rem] leading-relaxed">
          {s.decisions} decisions so far, using {s.model_calls} model {s.model_calls === 1 ? "call" : "calls"}. Spend{" "}
          {inr(s.cost_inr)} against{" "}
          {inr(s.baseline_inr)} if every one had gone to {s.baseline_model}:{" "}
          <strong className="font-semibold">{pct(s.savings_pct)} less</strong>.
          {s.cache_hits > 0 && ` The cache answered ${s.cache_hits} of them for free.`}
          {s.p50_latency_ms != null && ` Median model latency ${ms(s.p50_latency_ms)}.`}
        </p>
      )}

      {data && data.entries.length === 0 && (
        <p className="mt-6 border-t border-rule pt-6 text-graphite">
          No decisions yet. Run the analysis or ask a question; every decision and what it cost shows up here.
        </p>
      )}

      {data && data.entries.length > 0 && (
        <div className="mt-6 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-ink text-left text-[0.8rem] text-graphite">
                <th scope="col" className="py-2 pr-3 font-medium">Time</th>
                <th scope="col" className="py-2 pr-3 font-medium">Task</th>
                <th scope="col" className="py-2 pr-3 font-medium">Decided by</th>
                <th scope="col" className="py-2 pr-3 font-medium">Model</th>
                <th scope="col" className="py-2 pr-3 text-right font-medium">Tokens in / out</th>
                <th scope="col" className="py-2 pr-3 text-right font-medium">Cost</th>
                <th scope="col" className="py-2 pr-3 text-right font-medium">On the large model</th>
                <th scope="col" className="py-2 text-right font-medium">Latency</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {data.entries.map((e, i) => (
                <tr key={`${e.ts}-${i}`} className={`border-b border-rule ${e.accepted ? "" : "text-graphite"}`}>
                  <td className="py-2 pr-3 whitespace-nowrap">{time(e.ts)}</td>
                  <td className="py-2 pr-3">{e.task}</td>
                  <td className="py-2 pr-3 whitespace-nowrap">
                    {SOURCE_LABEL[e.source] ?? e.source}
                    {!e.accepted && " (rejected, escalated)"}
                  </td>
                  <td className="wd-condensed py-2 pr-3">{e.model}</td>
                  <td className="py-2 pr-3 text-right whitespace-nowrap">
                    {e.tokens_in.toLocaleString("en-IN")} / {e.tokens_out.toLocaleString("en-IN")}
                  </td>
                  <td className="py-2 pr-3 text-right">{inr(e.cost_usd * (s?.usd_inr ?? 0))}</td>
                  <td className="py-2 pr-3 text-right text-graphite">{inr(e.baseline_usd * (s?.usd_inr ?? 0))}</td>
                  <td className="py-2 text-right">{e.latency_ms ? ms(e.latency_ms) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
