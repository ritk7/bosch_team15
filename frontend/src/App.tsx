import { useCallback, useEffect, useRef, useState } from "react";
import { Moon, Sun, Maximize2, Minimize2, ShieldCheck } from "lucide-react";
import { CostGauge } from "./components/CostGauge";
import { TierStatus } from "./components/TierStatus";
import { api, type Experiment, type Health, type State, type Target } from "./lib/api";
import { LedgerView } from "./views/LedgerView";
import { Campaign } from "./views/Campaign";
import { Findings } from "./views/Findings";
import { Report } from "./views/Report";
import { Live } from "./views/Live";

const TABS = [
  { id: "live", label: "Live" },
  { id: "campaign", label: "Campaign" },
  { id: "findings", label: "Findings" },
  { id: "report", label: "Report" },
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
  const [targets, setTargets] = useState<{ name: string; title: string }[]>([]);
  const [switching, setSwitching] = useState(false);
  const [presenting, setPresenting] = useState(false);
  const [light, setLight] = useState(false);
  const requestEpoch = useRef(0);
  const metadataRequest = useRef(0);
  const polling = useRef(false);
  const switchingRef = useRef(false);

  const loadTarget = useCallback(async () => {
    const epoch = requestEpoch.current;
    const request = ++metadataRequest.current;
    const [nextTarget, nextCatalog, nextTargets] = await Promise.all([api.target(), api.catalog(), api.targets()]);
    if (epoch !== requestEpoch.current || request !== metadataRequest.current) return;
    setTarget(nextTarget);
    setCatalog(nextCatalog.experiments);
    setTargets(nextTargets.targets);
  }, []);

  async function switchTarget(name: string) {
    if (switchingRef.current) return;
    switchingRef.current = true;
    setSwitching(true);
    requestEpoch.current++;
    try {
      await api.selectTarget(name);
      setState(null); // never display the old target's measurements with the new target's map
      setTarget(null);
      setCatalog([]);
      await loadTarget();
      setState(await api.state());
      setOffline(false);
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    } finally {
      switchingRef.current = false;
      setSwitching(false);
    }
  }

  const poll = useCallback(async () => {
    if (polling.current || switchingRef.current) return;
    polling.current = true;
    const epoch = requestEpoch.current;
    try {
      const s = await api.state();
      if (epoch === requestEpoch.current) {
        setState(s);
        setOffline(false);
      }
    } catch {
      if (epoch === requestEpoch.current) setOffline(true);
    } finally {
      polling.current = false;
    }
  }, []);

  const refresh = useCallback(() => {
    void poll();
    void loadTarget().catch(() => setOffline(true)); // fixes change the graph and experiment catalog too
  }, [poll, loadTarget]);

  useEffect(() => {
    document.title = "Chaos Sentinel";
    const check = () => api.health().then(setHealth).catch(() => setHealth(null));
    check();
    void loadTarget().catch(() => setOffline(true));
    poll();
    const fast = setInterval(poll, 500);
    const slow = setInterval(check, 15000);
    return () => {
      requestEpoch.current++;
      clearInterval(fast);
      clearInterval(slow);
    };
  }, [poll, loadTarget]);

  useEffect(() => {
    if (state?.target && target?.name !== state.target) {
      void loadTarget().catch(() => setOffline(true));
    }
  }, [state?.target, target?.name, loadTarget]);

  const runner = state?.runner ?? health?.runner;

  return (
    <div data-theme={light ? "light" : "dark"} className={`app-shell min-h-dvh bg-paper text-ink ${presenting ? "presentation-mode" : ""}`}>
      <a href="#workspace" className="skip-link">Skip to workspace</a>
      <header className="app-header border-b border-rule bg-surface">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2 sm:px-6">
          <span className="wd-wide flex h-10 items-center gap-2.5 text-lg font-semibold">
            <span className="brand-icon"><ShieldCheck size={23} aria-hidden="true" /></span>
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
          {targets.length > 1 && target && (
            <label className="flex items-center gap-2 text-sm text-graphite">
              <span className="sr-only sm:not-sr-only">Testing</span>
              <select
                value={target.name}
                disabled={switching || offline || !!state?.busy || (state != null && !["idle", "done"].includes(state.phase))}
                onChange={(e) => switchTarget(e.target.value)}
                className="border border-rule bg-surface px-2 py-1 font-semibold text-ink"
              >
                {targets.map((t) => (
                  <option key={t.name} value={t.name}>{t.title}</option>
                ))}
              </select>
            </label>
          )}
          <nav aria-label="Views" className="view-nav flex gap-1">
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
          <div className="shell-controls">
            <button type="button" onClick={() => setLight(!light)} aria-label={light ? "Use dark theme" : "Use light theme"} title={light ? "Dark theme" : "Light theme"}>{light ? <Moon size={18} /> : <Sun size={18} />}</button>
            <button type="button" onClick={() => setPresenting(!presenting)} aria-label={presenting ? "Exit presentation mode" : "Enter presentation mode"} aria-pressed={presenting} title="Presentation mode">{presenting ? <Minimize2 size={18} /> : <Maximize2 size={18} />}</button>
          </div>
        </div>
      </header>
      <main id="workspace" tabIndex={-1} className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6 sm:py-8">
        {offline && state && <p role="alert" className="connection-warning">Connection lost. Displaying the last received state; live values may be stale.</p>}
        {offline && !state && (
          <p role="alert" className="border-l-2 border-crit pl-3 text-crit">
            The backend isn't reachable on port 8000. Start it with <code>python dev.py</code>.
          </p>
        )}
        {switching && <p role="status" className="text-graphite">Loading the selected target…</p>}
        {!switching && tab === "live" && state && target && <Live key={target.name} state={state} target={target} catalog={catalog} onChange={refresh} connected={!offline} />}
        {!switching && tab === "findings" && state && target && <Findings state={state} target={target} onChange={refresh} />}
        {!switching && tab === "campaign" && state && target && <Campaign state={state} target={target} catalog={catalog} onChange={refresh} />}
        {tab === "report" && state && <Report version={`${state.results.length}-${state.analysis?.score.value ?? ""}-${target?.name}`} />}
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
