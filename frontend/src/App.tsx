import { useEffect, useState } from "react";
import { TierStatus } from "./components/TierStatus";
import { api, type Health } from "./lib/api";
import { LedgerView } from "./views/LedgerView";

// Stub shell. The views to build (Live, Campaign, Findings, Report, Cost) are listed in docs/CONTRACT.md.
export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    const check = () =>
      api
        .health()
        .then((h) => {
          setHealth(h);
          setOffline(false);
        })
        .catch(() => setOffline(true));
    check();
    const id = setInterval(check, 15000);
    document.title = "Chaos Sentinel";
    return () => clearInterval(id);
  }, []);

  return (
    <div className="min-h-dvh bg-paper text-ink">
      <header className="border-b border-rule bg-surface">
        <div className="mx-auto flex max-w-[1240px] items-center gap-8 px-4 sm:px-6">
          <span className="wd-wide flex h-14 items-center text-lg font-semibold">Chaos Sentinel</span>
          <div className="ml-auto">
            <TierStatus health={health} offline={offline} />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1240px] px-4 py-8 sm:px-6 sm:py-10">
        <LedgerView />
      </main>
    </div>
  );
}
