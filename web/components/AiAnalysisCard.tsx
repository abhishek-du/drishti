"use client";

import { useCallback, useEffect, useState } from "react";
import { fmt, get } from "@/components/api";

type AIAnalysisResponse = {
  key: string;
  symbol: string;
  as_of: string;
  generated_at_ts: number;
  engine: string;
  bedrock_error?: string | null;
  markdown: string;
  facts?: {
    price: { ltp: number | null; change_pct: number | null; high: number | null; low: number | null };
    trends: Record<string, string | null>;
    technical: { ema20: number | null; dist_ema20_pct: number | null; rsi14: number | null; hi52: number | null; pct_below_52w_high: number | null };
    plan: { entry: number | null; stop: number | null; target: number | null; net_rr: number | null; risk_per_share: number | null };
    time_machine: { target_first: number | null; stop_first: number | null; neither: number | null; avg_r_net: number | null };
    scanner: { alerts_count: number };
    rule_verdict: { tag: string; tone: string };
  };
};

function parseMarkdown(md: string) {
  const lines = md.split("\n");
  const elements: React.ReactNode[] = [];
  let inTable = false;
  let tableHeader: string[] = [];
  let tableRows: string[][] = [];
  let keyIndex = 0;

  const flushTable = () => {
    if (inTable && tableHeader.length > 0) {
      elements.push(
        <div key={`table-${keyIndex++}`} style={{ overflowX: "auto", margin: "14px 0" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--card-hover)", borderBottom: "2px solid var(--line-strong)" }}>
                {tableHeader.map((th, i) => (
                  <th key={i} style={{ padding: "8px 12px", textAlign: i === 0 ? "left" : "left", color: "var(--fg)" }}>
                    {formatInline(th.trim())}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows.map((row, ri) => (
                <tr key={ri} style={{ borderBottom: "1px solid var(--line)", background: ri % 2 === 1 ? "rgba(255,255,255,0.02)" : "transparent" }}>
                  {row.map((cell, ci) => (
                    <td key={ci} style={{ padding: "8px 12px", verticalAlign: "top" }}>
                      {formatInline(cell.trim())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      tableHeader = [];
      tableRows = [];
      inTable = false;
    }
  };

  const formatInline = (text: string): React.ReactNode => {
    // Basic bold and pill formatting
    const parts = text.split(/(\*\*.*?\*\*|`.*?`)/g);
    return parts.map((part, idx) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={idx} style={{ color: "var(--fg)" }}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("`") && part.endsWith("`")) {
        return (
          <code key={idx} style={{ background: "var(--card-hover)", padding: "2px 5px", borderRadius: 4, fontSize: 12 }}>
            {part.slice(1, -1)}
          </code>
        );
      }
      return part;
    });
  };

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const line = raw.trim();

    // Check table
    if (line.startsWith("|") && line.endsWith("|")) {
      const cols = line.slice(1, -1).split("|");
      if (cols.every((c) => c.trim().match(/^:?-+:?$/))) {
        // Table divider line
        inTable = true;
        continue;
      }
      if (!inTable) {
        tableHeader = cols;
        inTable = true;
      } else {
        tableRows.push(cols);
      }
      continue;
    } else if (inTable) {
      flushTable();
    }

    if (!line) {
      continue;
    }

    // Headings
    if (line.startsWith("### ")) {
      elements.push(
        <h3 key={`h3-${keyIndex++}`} style={{ fontSize: 16, fontWeight: 700, margin: "18px 0 8px 0", color: "var(--brand)", display: "flex", alignItems: "center", gap: 6 }}>
          {line.replace("### ", "")}
        </h3>
      );
      continue;
    }
    if (line.startsWith("#### ")) {
      elements.push(
        <h4 key={`h4-${keyIndex++}`} style={{ fontSize: 14, fontWeight: 700, margin: "14px 0 6px 0", color: "var(--fg)" }}>
          {formatInline(line.replace("#### ", ""))}
        </h4>
      );
      continue;
    }

    // Blockquotes
    if (line.startsWith("> ")) {
      elements.push(
        <div
          key={`bq-${keyIndex++}`}
          style={{
            background: "rgba(59, 130, 246, 0.08)",
            borderLeft: "4px solid var(--brand)",
            padding: "8px 14px",
            margin: "8px 0",
            borderRadius: "0 6px 6px 0",
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          {formatInline(line.replace("> ", ""))}
        </div>
      );
      continue;
    }

    // Bullet lists
    if (line.startsWith("- ")) {
      elements.push(
        <div key={`li-${keyIndex++}`} style={{ display: "flex", alignItems: "flex-start", gap: 8, margin: "4px 0 4px 12px", fontSize: 13, lineHeight: 1.5 }}>
          <span style={{ color: "var(--brand)", fontWeight: "bold" }}>•</span>
          <div style={{ flex: 1 }}>{formatInline(line.replace("- ", ""))}</div>
        </div>
      );
      continue;
    }

    // Horizontal Rule
    if (line === "---") {
      elements.push(<hr key={`hr-${keyIndex++}`} style={{ border: "none", borderTop: "1px solid var(--line)", margin: "14px 0" }} />);
      continue;
    }

    // Standard paragraph
    elements.push(
      <p key={`p-${keyIndex++}`} style={{ margin: "6px 0", fontSize: 13, lineHeight: 1.5, color: line.startsWith("*(") ? "var(--muted)" : "var(--fg)" }}>
        {formatInline(line)}
      </p>
    );
  }
  flushTable();

  return elements;
}

export default function AiAnalysisCard({ instrumentKey }: { instrumentKey: string }) {
  const [data, setData] = useState<AIAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [open, setOpen] = useState(true);

  const fetchAnalysis = useCallback(
    (force = false) => {
      setLoading(true);
      get<AIAnalysisResponse>(`/api/analysis/ai?key=${encodeURIComponent(instrumentKey)}${force ? "&force=true" : ""}`)
        .then((res) => {
          setData(res);
          setErr(null);
        })
        .catch((e) => {
          setErr(String(e));
        })
        .finally(() => {
          setLoading(false);
        });
    },
    [instrumentKey]
  );

  useEffect(() => {
    fetchAnalysis(false);
  }, [fetchAnalysis]);

  const copyMarkdown = () => {
    if (data?.markdown) {
      navigator.clipboard.writeText(data.markdown).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    }
  };

  if (err && !data) {
    return (
      <div className="card" style={{ marginTop: 12, padding: "12px 16px", borderColor: "var(--down-border)" }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <span className="err">⚠️ AI Intelligence unavailable: {err}</span>
          <button className="btn small" onClick={() => fetchAnalysis(true)}>Retry</button>
        </div>
      </div>
    );
  }

  const f = data?.facts;
  const t = f?.trends;
  const tripleUp = t?.["1d"] === "UP" && t?.["1h"] === "UP" && t?.["15m"] === "UP";
  const rulePass = f?.rule_verdict?.tag?.includes("BUY") && !f?.rule_verdict?.tag?.includes("DON'T");

  return (
    <section
      className="card"
      style={{
        marginTop: 12,
        padding: "14px 18px",
        background: "var(--card)",
        border: "1px solid rgba(59, 130, 246, 0.25)",
        boxShadow: "var(--shadow-md)",
      }}
    >
      {/* Header Bar */}
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10, marginBottom: 12 }}>
        <div className="row" style={{ alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 18 }}>🤖</span>
          <div>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "var(--fg)" }}>
              Drishti AI Trading Intelligence
            </h3>
            <span className="muted small" style={{ fontSize: 11 }}>
              Complete 360° Quantitative Breakdown &amp; Trade Playbook
            </span>
          </div>
          {data?.engine && (
            <span
              className="pill"
              style={{
                fontSize: 11,
                padding: "2px 8px",
                background: data.engine.includes("Bedrock") && !data.engine.includes("offline") ? "rgba(16, 185, 129, 0.15)" : "rgba(59, 130, 246, 0.12)",
                color: data.engine.includes("Bedrock") && !data.engine.includes("offline") ? "var(--up)" : "var(--accent)",
                borderColor: data.engine.includes("Bedrock") && !data.engine.includes("offline") ? "var(--up-border)" : "rgba(59, 130, 246, 0.3)",
              }}
              title={data.bedrock_error ?? data.engine}
            >
              ⚡ {data.engine.includes("Bedrock") && !data.engine.includes("offline") ? "Amazon Bedrock Nova Pro" : "Quantitative Engine"}
            </span>
          )}
        </div>

        <div className="row" style={{ alignItems: "center", gap: 8 }}>
          {data?.as_of && (
            <span className="muted small" style={{ fontSize: 12 }}>
              Updated {new Date(data.as_of).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit" })} IST
            </span>
          )}
          <button
            className="btn small"
            onClick={() => fetchAnalysis(true)}
            disabled={loading}
            style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}
            title="Force regenerate AI intelligence with latest data"
          >
            {loading ? "Refreshing…" : "🔄 Refresh"}
          </button>
          <button
            className="btn small"
            onClick={copyMarkdown}
            style={{ cursor: "pointer" }}
            title="Copy full markdown report"
          >
            {copied ? "✓ Copied" : "📋 Copy"}
          </button>
          <button
            className="btn small"
            onClick={() => setOpen(!open)}
            style={{ cursor: "pointer" }}
          >
            {open ? "▲ Less" : "▼ More"}
          </button>
        </div>
      </div>

      {/* Quick Summary Pill Banner */}
      {f && (
        <div
          className="row"
          style={{
            background: "var(--card-hover)",
            padding: "8px 12px",
            borderRadius: 6,
            marginBottom: open ? 14 : 0,
            gap: 10,
            flexWrap: "wrap",
            alignItems: "center",
            fontSize: 12,
          }}
        >
          <span style={{ fontWeight: 600, color: "var(--muted)" }}>Quick Snapshot:</span>

          <span className={`pill ${tripleUp ? "on" : t?.["1d"] === "UP" ? "on" : ""}`} style={{ fontSize: 11 }}>
            {tripleUp ? "🟢 Triple Trend UP (1D·1H·15M)" : `1D: ${t?.["1d"] ?? "–"} · 1H: ${t?.["1h"] ?? "–"} · 15M: ${t?.["15m"] ?? "–"}`}
          </span>

          {f.technical.pct_below_52w_high !== null && (
            <span className="pill" style={{ fontSize: 11, borderColor: f.technical.pct_below_52w_high <= 1.0 ? "var(--up-border)" : undefined }}>
              52W High: {f.technical.pct_below_52w_high <= 0.5 ? "🔥 At High" : `-${fmt(f.technical.pct_below_52w_high)}%`}
            </span>
          )}

          {f.rule_verdict?.tag && (
            <span className={`pill ${rulePass ? "on" : "off"}`} style={{ fontSize: 11, fontWeight: 700 }}>
              {f.rule_verdict.tag}
            </span>
          )}

          {f.time_machine && (
            <span className="pill" style={{ fontSize: 11 }}>
              Odds: Tgt {fmt((f.time_machine.target_first ?? 0) * 100, 0)}% · Stop {fmt((f.time_machine.stop_first ?? 0) * 100, 0)}% · Neither {fmt((f.time_machine.neither ?? 0) * 100, 0)}%
            </span>
          )}

          {f.technical.dist_ema20_pct !== null && (
            <span className="pill warn" style={{ fontSize: 11 }}>
              EMA20: {f.technical.dist_ema20_pct >= 0 ? "+" : ""}{fmt(f.technical.dist_ema20_pct)}%
            </span>
          )}

          {f.scanner.alerts_count > 0 && (
            <span className="pill on" style={{ fontSize: 11 }}>
              ⚡ {f.scanner.alerts_count} Alert(s) Today
            </span>
          )}
        </div>
      )}

      {/* Main Breakdown Content */}
      {open && (
        <div style={{ marginTop: 10 }}>
          {loading && !data ? (
            <div style={{ padding: "20px 0", textAlign: "center" }}>
              <p className="muted">Analyzing multi-timeframe price action, order flow &amp; statistical odds…</p>
            </div>
          ) : data?.markdown ? (
            parseMarkdown(data.markdown)
          ) : (
            <p className="muted">No AI intelligence generated yet.</p>
          )}
        </div>
      )}
    </section>
  );
}
