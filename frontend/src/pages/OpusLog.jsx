import { useState, useEffect } from "react";
import { api } from "../api.js";

const PRIORITY_COLOR = {
  P0: { bg: "#450a0a", color: "#fca5a5", label: "Kritis" },
  P1: { bg: "#422006", color: "#fcd34d", label: "Minggu ini" },
  P2: { bg: "#1a2e1a", color: "#86efac", label: "Opsional" },
};

export default function OpusLog() {
  const [evaluations, setEvaluations] = useState([]);
  const [selected,    setSelected]    = useState(0);
  const [loading,     setLoading]     = useState(true);

  useEffect(() => {
    api.opusMemory(8)
      .then(d => {
        setEvaluations(d?.evaluations ?? []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return <div style={s.loading}>Memuat Opus log...</div>;

  if (evaluations.length === 0) {
    return (
      <div>
        <h1 style={s.title}>Opus Log</h1>
        <div style={s.empty}>
          Belum ada evaluasi Opus. Opus berjalan setiap Senin 08:00 WIB.
        </div>
      </div>
    );
  }

  const ev      = evaluations[selected];
  const summary = ev?.summary ?? {};
  const actions = ev?.actions_required ?? [];
  const updated = ev?.params_updated ?? {};
  const p0 = actions.filter(a => a.priority === "P0");
  const p1 = actions.filter(a => a.priority === "P1");
  const p2 = actions.filter(a => a.priority === "P2");

  return (
    <div>
      <div style={s.header}>
        <div>
          <h1 style={s.title}>Opus Log</h1>
          <p style={s.desc}>Evaluasi mingguan — Senin 08:00 WIB</p>
        </div>
        {/* Week selector */}
        <div style={s.weekTabs}>
          {evaluations.map((e, i) => (
            <button
              key={e.week_start}
              onClick={() => setSelected(i)}
              style={{
                ...s.weekTab,
                ...(i === selected ? s.weekTabActive : {}),
              }}
            >
              {e.week_start?.slice(5)}
            </button>
          ))}
        </div>
      </div>

      {/* Summary metrics */}
      <div style={s.grid5}>
        <Metric label="Win rate"    value={`${((summary.win_rate ?? 0) * 100).toFixed(1)}%`}
                color={summary.win_rate >= 0.6 ? "#4ade80" : summary.win_rate >= 0.5 ? "#fbbf24" : "#f87171"} />
        <Metric label="Total PnL"   value={`$${parseFloat(summary.total_pnl ?? 0).toFixed(2)}`}
                color={summary.total_pnl >= 0 ? "#4ade80" : "#f87171"} />
        <Metric label="Max DD"      value={`${((summary.max_drawdown ?? 0) * 100).toFixed(1)}%`}
                color={summary.max_drawdown > 0.10 ? "#f87171" : "#4ade80"} />
        <Metric label="Trades"      value={summary.total_trades ?? 0} />
        <Metric label="Sharpe"      value={parseFloat(summary.sharpe_ratio ?? 0).toFixed(2)}
                color={summary.sharpe_ratio >= 1.0 ? "#4ade80" : "#fbbf24"} />
      </div>

      {/* Assessment */}
      {summary.assessment && (
        <div style={s.assessment}>{summary.assessment}</div>
      )}

      {/* Action required */}
      {actions.length > 0 && (
        <Section title={`Action required (${actions.length})`}>
          {[...p0, ...p1, ...p2].map((action, i) => (
            <ActionCard key={i} action={action} />
          ))}
        </Section>
      )}

      {/* Auto-updated params */}
      {Object.keys(updated).length > 0 && (
        <Section title="Parameter yang diupdate otomatis oleh Opus">
          <div style={s.paramGrid}>
            {Object.entries(updated).map(([pair, params]) => (
              <div key={pair} style={s.paramCard}>
                <div style={s.paramPair}>{pair}</div>
                {Object.entries(params).map(([k, v]) => (
                  <div key={k} style={s.paramRow}>
                    <span style={s.paramKey}>{k}</span>
                    <span style={s.paramVal}>{JSON.stringify(v)}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Cost info */}
      <div style={s.costRow}>
        <span style={s.costLabel}>Biaya evaluasi ini:</span>
        <span style={s.costVal}>${parseFloat(ev.token_cost ?? 0).toFixed(4)}</span>
        <span style={s.costLabel}>· Periode:</span>
        <span style={s.costVal}>{ev.week_start} → {ev.week_end}</span>
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Metric({ label, value, color = "#e2e8f0" }) {
  return (
    <div style={s.metricCard}>
      <div style={s.metricLabel}>{label}</div>
      <div style={{ ...s.metricVal, color }}>{value}</div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={s.section}>
      <div style={s.sectionTitle}>{title}</div>
      {children}
    </div>
  );
}

function ActionCard({ action }) {
  const pri = action.priority ?? "P2";
  const c   = PRIORITY_COLOR[pri] ?? PRIORITY_COLOR.P2;

  return (
    <div style={{ ...s.actionCard, background: c.bg }}>
      <div style={s.actionHeader}>
        <span style={{ ...s.priBadge, color: c.color }}>{pri} — {c.label}</span>
        <span style={s.actionTitle}>{action.title}</span>
      </div>
      {action.problem && (
        <p style={s.actionProblem}>{action.problem}</p>
      )}
      {(action.steps ?? []).length > 0 && (
        <ol style={s.steps}>
          {action.steps.map((step, i) => (
            <li key={i} style={s.step}>
              <span style={s.stepAction}>{step.action}</span>
              {step.file && (
                <code style={s.stepFile}>{step.file}</code>
              )}
              {step.change && (
                <span style={s.stepChange}>{step.change}</span>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

const s = {
  title:   { fontSize: 20, fontWeight: 700, color: "#e2e8f0", marginBottom: 4 },
  desc:    { fontSize: 13, color: "#64748b" },
  loading: { fontSize: 13, color: "#64748b", padding: "40px 0" },
  empty:   { fontSize: 13, color: "#475569", padding: "20px 0" },
  header: {
    display: "flex", justifyContent: "space-between",
    alignItems: "flex-start", marginBottom: 20, gap: 20,
  },
  weekTabs: { display: "flex", gap: 4, flexWrap: "wrap" },
  weekTab: {
    background: "#161b27", border: "1px solid #1e2535",
    borderRadius: 6, color: "#64748b", padding: "4px 10px",
    fontSize: 11, cursor: "pointer",
  },
  weekTabActive: {
    background: "#1e3a5f", borderColor: "#2563eb44", color: "#60a5fa",
  },
  grid5: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
    gap: 10, marginBottom: 16,
  },
  metricCard: {
    background: "#161b27", border: "1px solid #1e2535",
    borderRadius: 8, padding: "10px 12px",
  },
  metricLabel: { fontSize: 10, color: "#64748b", letterSpacing: "0.05em",
                 textTransform: "uppercase", marginBottom: 4 },
  metricVal:   { fontSize: 18, fontWeight: 700, letterSpacing: "-0.02em" },
  assessment: {
    background: "#161b27", border: "1px solid #1e2535",
    borderRadius: 8, padding: "12px 14px",
    fontSize: 13, color: "#94a3b8", fontStyle: "italic",
    marginBottom: 20, lineHeight: 1.6,
  },
  section: {
    background: "#161b27", border: "1px solid #1e2535",
    borderRadius: 10, padding: "14px 16px", marginBottom: 20,
  },
  sectionTitle: {
    fontSize: 12, fontWeight: 600, color: "#94a3b8",
    letterSpacing: "0.05em", textTransform: "uppercase", marginBottom: 12,
  },
  actionCard: {
    borderRadius: 8, padding: "12px 14px", marginBottom: 10,
  },
  actionHeader: { display: "flex", alignItems: "center", gap: 10, marginBottom: 6 },
  priBadge:     { fontSize: 10, fontWeight: 700, letterSpacing: "0.06em" },
  actionTitle:  { fontSize: 13, fontWeight: 600, color: "#e2e8f0" },
  actionProblem:{ fontSize: 12, color: "#94a3b8", lineHeight: 1.5, marginBottom: 8 },
  steps:   { paddingLeft: 20, display: "flex", flexDirection: "column", gap: 6 },
  step:    { fontSize: 12, color: "#cbd5e1", lineHeight: 1.6 },
  stepAction: { display: "block", color: "#cbd5e1" },
  stepFile: {
    display: "inline-block", fontSize: 11,
    background: "#0d111a", color: "#60a5fa",
    padding: "1px 6px", borderRadius: 4, margin: "0 6px",
    fontFamily: "monospace",
  },
  stepChange: { fontSize: 11, color: "#94a3b8" },
  paramGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
    gap: 10,
  },
  paramCard: {
    background: "#0d111a", border: "1px solid #1e2535",
    borderRadius: 8, padding: "10px 12px",
  },
  paramPair: { fontSize: 12, fontWeight: 600, color: "#e2e8f0", marginBottom: 8 },
  paramRow:  { display: "flex", justifyContent: "space-between",
               fontSize: 11, marginBottom: 4 },
  paramKey:  { color: "#64748b" },
  paramVal:  { color: "#4ade80", fontFamily: "monospace" },
  costRow: {
    display: "flex", gap: 8, alignItems: "center",
    fontSize: 11, color: "#475569",
  },
  costLabel: { color: "#475569" },
  costVal:   { color: "#64748b", fontFamily: "monospace" },
};
