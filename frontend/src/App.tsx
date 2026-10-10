import { useCallback, useEffect, useState } from "react";
import { CostGauge } from "./components/CostGauge";
import { TierStatus } from "./components/TierStatus";
import { api, type Experiment, type Health, type State, type Target } from "./lib/api";
import { LedgerView } from "./views/LedgerView";
import { Live } from "./views/Live";

const TABS = [
  { id: "live", label: "Live" },
  { id: "cost", label: "Cost" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  const [state, setState] = useState<State | null>(null);
  const [target, setTarget] = useState<Target | null>(null);
  const [catalog, setCatalog] = useState<Experiment[]>([]);
  const [tab, setTab] = useState<Tab>("live");

  const poll = useCallback(() => {
    api
      .state()
      .then((s) => {
        setState(s);
        setOffline(false);
      })
      .catch(() => setOffline(true));
  }, []);

  useEffect(() => {
    document.title = "Chaos Sentinel";
    const check = () => api.health().then(setHealth).catch(() => setOffline(true));
    check();
    api.target().then(setTarget).catch(() => setOffline(true));
    api.catalog().then((c) => setCatalog(c.experiments)).catch(() => undefined);
    poll();
    const fast = setInterval(poll, 500);
    const slow = setInterval(check, 15000);
    return () => {
      clearInterval(fast);
      clearInterval(slow);
    };
  }, [poll]);

  const runner = state?.runner ?? health?.runner;

  return (
    <div className="min-h-dvh bg-paper text-ink">
      <header className="border-b border-rule bg-surface">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2 sm:px-6">
          <span className="wd-wide flex h-10 items-center gap-2.5 text-lg font-semibold">
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            Chaos Sentinel
          </span>
          {runner && (
            <span
              title={runner.detail}
              className={`border px-2 py-0.5 text-[0.8rem] font-semibold ${runner.kind === "sim" ? "border-warn text-warn" : "border-ok text-ok"}`}
            >
              {runner.kind === "sim" ? "Simulation" : "Docker"}
            </span>
          )}
          <nav aria-label="Views" className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                aria-current={tab === t.id ? "page" : undefined}
                className={`px-3 py-2 text-sm ${tab === t.id ? "font-semibold text-ink underline decoration-cobalt decoration-2 underline-offset-[10px]" : "text-graphite hover:text-ink"}`}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <div className="ml-auto">
            <TierStatus health={health} offline={offline} />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6 sm:py-8">
        {offline && !state && (
          <p role="alert" className="border-l-2 border-crit pl-3 text-crit">
            The backend isn't reachable on port 8000. Start it with <code>python dev.py</code>.
          </p>
        )}
        {tab === "live" && state && target && <Live state={state} target={target} catalog={catalog} onChange={poll} />}
        {tab === "cost" && (
          <div className="space-y-10">
            {state && <CostGauge cost={state.cost} runKey={String(state.results.length)} />}
            <LedgerView />
          </div>
        )}
      </main>
    </div>
  );
}
