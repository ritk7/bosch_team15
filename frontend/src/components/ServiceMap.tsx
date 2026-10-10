import type { Graph, GraphNode, ServiceState, ServiceStatus } from "../lib/api";

// Dependency map. Callers sit above the services they call; each node shows its live state as a word and a mark,
// so colour is never the only signal.
const W = 116;
const H = 62;
const VIEW_W = 420;
const ROW_GAP = 150;
const TOP = 26;

interface Props {
  graph: Graph;
  services: ServiceStatus[];
  selected: string | null;
  target: string | null; // the service under fault right now
  disabled: boolean;
  flowing?: boolean; // animate units moving along the data flows
  onSelect: (id: string) => void;
}

function layout(graph: Graph) {
  const out = new Map<string, string[]>();
  for (const n of graph.nodes) out.set(n.id, []);
  for (const e of graph.edges) out.get(e.from)?.push(e.to);
  const depth = new Map<string, number>();
  const visit = (id: string, seen: Set<string>): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0;
    seen.add(id);
    const d = Math.max(-1, ...(out.get(id) ?? []).map((t) => visit(t, seen))) + 1;
    depth.set(id, d);
    return d;
  };
  graph.nodes.forEach((n) => visit(n.id, new Set()));
  const maxDepth = Math.max(0, ...depth.values());
  const rows: GraphNode[][] = Array.from({ length: maxDepth + 1 }, () => []);
  graph.nodes.forEach((n) => rows[maxDepth - depth.get(n.id)!].push(n));

  const pos = new Map<string, { x: number; y: number }>();
  const place = (row: GraphNode[], r: number) => {
    const step = VIEW_W / row.length;
    row.forEach((n, i) => pos.set(n.id, { x: step * i + step / 2, y: TOP + r * ROW_GAP + H / 2 }));
  };
  // bottom row first in compose order, then order each row above by where its callees sit (fewer crossings)
  for (let r = rows.length - 1; r >= 0; r--) {
    if (r < rows.length - 1) {
      const centre = (n: GraphNode) => {
        const xs = (out.get(n.id) ?? []).map((t) => pos.get(t)?.x).filter((x): x is number => x != null);
        return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : VIEW_W / 2;
      };
      rows[r].sort((a, b) => centre(a) - centre(b));
    }
    place(rows[r], r);
  }
  return { pos, height: TOP * 2 + (rows.length - 1) * ROW_GAP + H };
}

const STATE_WORD: Record<ServiceState, string> = {
  running: "running",
  stopped: "stopped",
  paused: "paused",
  unhealthy: "unhealthy",
  restarting: "restarting",
  degraded: "cut off",
};

function tone(state: ServiceState | undefined, health: string | undefined) {
  if (!state) return "text-graphite";
  if (state === "stopped") return "text-crit";
  if (state === "running" && health !== "unhealthy") return "text-ok";
  return "text-warn";
}

function Mark({ state, x, y }: { state: ServiceState | undefined; x: number; y: number }) {
  if (state === "stopped")
    return (
      <g stroke="currentColor" strokeWidth={2} strokeLinecap="round">
        <line x1={x - 4} y1={y - 4} x2={x + 4} y2={y + 4} />
        <line x1={x - 4} y1={y + 4} x2={x + 4} y2={y - 4} />
      </g>
    );
  if (state === "paused")
    return (
      <g fill="currentColor">
        <rect x={x - 4} y={y - 4.5} width={3} height={9} />
        <rect x={x + 1} y={y - 4.5} width={3} height={9} />
      </g>
    );
  if (state === "running") return <circle cx={x} cy={y} r={4.5} fill="currentColor" />;
  return <circle cx={x} cy={y} r={4} fill="none" stroke="currentColor" strokeWidth={2} />;
}

export function ServiceMap({ graph, services, selected, target, disabled, flowing = true, onSelect }: Props) {
  const { pos, height } = layout(graph);
  const status = new Map(services.map((s) => [s.id, s]));
  // draw one line per pair, preferring the data flow over the start-order edge
  const pairs = new Map<string, Graph["edges"][number]>();
  for (const e of graph.edges) {
    const key = `${e.from}>${e.to}`;
    if (!pairs.has(key) || e.kind === "dataflow") pairs.set(key, e);
  }

  return (
    <figure className="m-0">
      <svg viewBox={`0 0 ${VIEW_W} ${height}`} className="block h-auto w-full" role="group" aria-label="Service dependency map">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" className="fill-graphite" />
          </marker>
        </defs>
        {/* living map: units flow along data flows; at a dead or frozen service they stop and pile up */}
        {[...pairs.values()].filter((e) => e.kind === "dataflow").map((e) => {
          const a = pos.get(e.from)!;
          const b = pos.get(e.to)!;
          const x1 = a.x, y1 = a.y + H / 2, x2 = b.x, y2 = b.y - H / 2 - 3;
          const dst = status.get(e.to);
          const src = status.get(e.from);
          const blocked = !dst || dst.state !== "running" || dst.health === "unhealthy";
          const idle = !src || src.state !== "running";
          if (!flowing || idle) return null;
          if (blocked) {
            return (
              <g key={`pile-${e.from}-${e.to}`} className="fill-crit" aria-hidden="true">
                {[0, 1, 2, 3, 4].map((k) => {
                  const f = 0.86 - k * 0.07;
                  return <circle key={k} cx={x1 + (x2 - x1) * f + (k % 2 ? 3 : -3)} cy={y1 + (y2 - y1) * f} r={2.6} className="animate-pulse" />;
                })}
              </g>
            );
          }
          const d = `M${x1},${y1} L${x2},${y2}`;
          return (
            <g key={`flow-${e.from}-${e.to}`} aria-hidden="true">
              {[0, 1, 2].map((k) => (
                <circle key={k} r={2.6} className="fill-cobalt">
                  <animateMotion dur="1.8s" begin={`${k * 0.6}s`} repeatCount="indefinite" path={d} />
                </circle>
              ))}
            </g>
          );
        })}
        {[...pairs.values()].map((e) => {
          const a = pos.get(e.from)!;
          const b = pos.get(e.to)!;
          const hot = target != null && (e.from === target || e.to === target);
          return (
            <line
              key={`${e.from}-${e.to}`}
              x1={a.x}
              y1={a.y + H / 2}
              x2={b.x}
              y2={b.y - H / 2 - 3}
              className={hot ? "stroke-crit" : "stroke-graphite"}
              strokeWidth={e.kind === "dataflow" ? 1.5 : 1}
              strokeDasharray={e.kind === "dataflow" ? undefined : "4 4"}
              markerEnd="url(#arrow)"
            >
              <title>{`${e.from} → ${e.to}: ${e.label}`}</title>
            </line>
          );
        })}
        {graph.nodes.map((n) => {
          const p = pos.get(n.id)!;
          const s = status.get(n.id);
          const isSel = selected === n.id;
          const blast = target != null && n.id !== target && graph.edges.some((e) => e.kind === "dataflow" && e.from === n.id && e.to === target);
          const x = p.x - W / 2;
          const y = p.y - H / 2;
          const word = s ? STATE_WORD[s.state] + (s.state === "running" && s.health === "unhealthy" ? ", starting" : "") : "unknown";
          return (
            <g
              key={n.id}
              role="button"
              tabIndex={disabled ? -1 : 0}
              aria-pressed={isSel}
              aria-disabled={disabled}
              aria-label={`${n.id}, ${n.role}, ${word}. Choose a failure for it.`}
              onClick={() => !disabled && onSelect(n.id)}
              onKeyDown={(ev) => {
                if (!disabled && (ev.key === "Enter" || ev.key === " ")) {
                  ev.preventDefault();
                  onSelect(n.id);
                }
              }}
              className={`group outline-none ${disabled ? "cursor-default" : "cursor-pointer"}`}
            >
              {isSel && <rect x={x - 5} y={y - 5} width={W + 10} height={H + 10} rx={8} className="fill-none stroke-cobalt" strokeWidth={2} />}
              {blast && <rect x={x - 4} y={y - 4} width={W + 8} height={H + 8} rx={7} className="fill-none stroke-warn" strokeWidth={1.5} strokeDasharray="3 3"><title>In the blast radius: depends on {target}</title></rect>}
              <rect
                x={x}
                y={y}
                width={W}
                height={H}
                rx={5}
                className={`fill-surface ${s?.state === "stopped" ? "stroke-crit" : "stroke-rule"} ${
                  disabled ? "" : "group-hover:stroke-cobalt group-focus-visible:stroke-cobalt"
                }`}
                strokeWidth={s?.state === "stopped" ? 2 : 1.25}
              />
              {s?.state === "stopped" && (
                <rect x={x} y={y} width={W} height={H} rx={5} className="pointer-events-none fill-crit/8" />
              )}
              <text x={x + 12} y={y + 22} className="wd-wide fill-ink text-[15px] font-semibold">
                {n.id}
              </text>
              <text x={x + W - 10} y={y + 22} textAnchor="end" className="fill-graphite text-[11px]">
                {n.role}
              </text>
              <g className={tone(s?.state, s?.health)}>
                <Mark state={s?.state} x={x + 16} y={y + 42} />
                <text x={x + 27} y={y + 46} className="fill-current text-[12px]">
                  {word}
                </text>
              </g>
              {n.spof && (
                <text x={x + W - 10} y={y + 46} textAnchor="end" className="fill-graphite text-[10px]">
                  <title>Single point of failure: one replica, used by {n.fan_in} services</title>
                  SPOF
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-graphite">
        <span className="flex items-center gap-1.5">
          <svg width="22" height="6" aria-hidden="true"><line x1="0" y1="3" x2="22" y2="3" className="stroke-graphite" strokeWidth="1.5" /></svg>
          data flow
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="22" height="6" aria-hidden="true"><line x1="0" y1="3" x2="22" y2="3" className="stroke-graphite" strokeDasharray="4 4" /></svg>
          starts after
        </span>
        <span className="flex items-center gap-1.5"><svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="2.6" className="fill-cobalt" /></svg>units flowing</span>
        <span className="flex items-center gap-1.5"><svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="2.6" className="fill-crit" /></svg>piling up at a broken service</span>
        <span className="flex items-center gap-1.5"><svg width="14" height="10" aria-hidden="true"><rect x="1" y="1" width="12" height="8" rx="2" className="fill-none stroke-warn" strokeDasharray="3 3" /></svg>blast radius</span>
        <span>SPOF = single point of failure</span>
      </figcaption>
    </figure>
  );
}
