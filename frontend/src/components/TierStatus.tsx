import type { Health } from "../lib/api";

const NAMES = { local: "Local model", small: "Small model", large: "Large model" } as const;

export function TierStatus({ health, offline }: { health: Health | null; offline: boolean }) {
  if (offline) return <p className="text-sm text-crit">Backend not reachable</p>;
  if (!health) return null;
  return (
    <ul aria-label="Model tiers" className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-graphite">
      {(["local", "small", "large"] as const).map((name) => {
        const tier = health.tiers[name];
        const state = tier.available ? "ready" : tier.configured ? "not responding" : "not set up";
        return (
          <li key={name} title={`${tier.model || "no model set"}: ${state}`} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className={`h-2 w-2 rounded-full ${tier.available ? "bg-ok" : tier.configured ? "border border-warn" : "border border-graphite"}`}
            />
            {NAMES[name]}
            <span className="sr-only">: {state}</span>
          </li>
        );
      })}
    </ul>
  );
}
