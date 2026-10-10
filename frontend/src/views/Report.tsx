import { useEffect, useState } from "react";
import { api } from "../lib/api";

// Renders the generated markdown report (headings, tables, lists, code) without a markdown dependency.
function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const out: React.ReactNode[] = [];
  let i = 0;
  const inline = (s: string) =>
    s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, k) =>
      part.startsWith("**") ? <strong key={k}>{part.slice(2, -2)}</strong> : part.startsWith("`") ? <code key={k} className="wd-condensed bg-paper px-1">{part.slice(1, -1)}</code> : part,
    );
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      out.push(<pre key={i} className="wd-condensed my-3 overflow-x-auto bg-paper p-3 text-[0.8rem]">{body.join("\n")}</pre>);
    } else if (l.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        if (!/^\|[-:| ]+\|$/.test(lines[i])) rows.push(lines[i].slice(1, -1).split("|").map((c) => c.trim()));
        i++;
      }
      i--;
      out.push(
        <div key={i} className="my-3 overflow-x-auto"><table className="w-full border-collapse text-[0.8rem]">
          <thead><tr className="border-b border-ink text-left text-graphite">{rows[0].map((c, k) => <th key={k} className="py-1.5 pr-3 font-medium">{c}</th>)}</tr></thead>
          <tbody className="tabular-nums">{rows.slice(1).map((r, k) => <tr key={k} className="border-b border-rule">{r.map((c, j) => <td key={j} className="py-1.5 pr-3">{inline(c)}</td>)}</tr>)}</tbody>
        </table></div>,
      );
    } else if (l.startsWith("# ")) out.push(<h1 key={i} className="wd-wide mt-2 text-[1.6rem] font-semibold">{l.slice(2)}</h1>);
    else if (l.startsWith("## ")) out.push(<h2 key={i} className="wd-wide mt-8 border-t border-rule pt-4 text-lg font-semibold">{l.slice(3)}</h2>);
    else if (l.startsWith("### ")) out.push(<h3 key={i} className="mt-5 font-semibold">{l.slice(4)}</h3>);
    else if (l.startsWith("- ")) out.push(<li key={i} className="ml-5 list-disc text-sm">{inline(l.slice(2))}</li>);
    else if (l.trim()) out.push(<p key={i} className="my-2 max-w-[90ch] text-sm leading-relaxed">{inline(l)}</p>);
    i++;
  }
  return <div>{out}</div>;
}

export function Report({ version }: { version: string }) {
  const [md, setMd] = useState<string>("");
  useEffect(() => {
    api.report().then((r) => setMd(r.markdown)).catch(() => setMd("The backend is not reachable."));
  }, [version]);
  return (
    <div>
      <div className="flex flex-wrap items-center justify-end gap-3 text-sm">
        <a href="/api/report.md" className="bg-cobalt px-4 py-2 font-semibold text-on-cobalt hover:opacity-90">Download findings report</a>
        <a href="/api/architecture.md" className="border border-cobalt px-4 py-2 font-semibold text-cobalt hover:bg-cobalt-soft">Download architecture overview</a>
      </div>
      <article className="mt-4 border border-rule bg-surface p-5 sm:p-8"><Markdown text={md} /></article>
    </div>
  );
}
