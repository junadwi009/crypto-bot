import { useState } from "react";
import { Card, Badge, Empty } from "../ui/index.jsx";
import { useOpusMemory } from "../../hooks/useData.js";

const P_COLOR = {
  P0: { bg: "rgba(255,71,87,0.12)",   border: "rgba(255,71,87,0.3)",   text: "#ff4757", label: "CRITICAL"  },
  P1: { bg: "rgba(255,211,42,0.1)",   border: "rgba(255,211,42,0.25)", text: "#ffd32a", label: "THIS WEEK" },
  P2: { bg: "rgba(0,212,170,0.08)",   border: "rgba(0,212,170,0.2)",   text: "#00d4aa", label: "OPTIONAL"  },
};

function ActionCard({ action }) {
  const [open, setOpen] = useState(false);
  const c = P_COLOR[action.priority] || P_COLOR.P2;
  return (
    <div style={{
      borderRadius: "var(--radius-md)",
      border: `1px solid ${c.border}`,
      background: c.bg,
      overflow: "hidden",
      marginBottom: 6,
    }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "9px 12px", cursor: "pointer",
        }}
      >
        <span style={{ fontSize: 9, fontWeight: 800, color: c.text,
                        fontFamily: "var(--font-mono)", letterSpacing: "0.1em" }}>
          {action.priority} · {c.label}
        </span>
        <span style={{ fontSize: 12, color: "var(--text-primary)", flex: 1 }}>
          {action.title}
        </span>
        <span style={{ color: "var(--text-muted)", fontSize: 12 }}>{open ? "▲" : "▼"}</span>
      </div>
      {open && (
        <div style={{ padding: "0 12px 10px", borderTop: `1px solid ${c.border}` }}>
          {action.problem && (
            <p style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.6,
                        margin: "8px 0 6px" }}>
              {action.problem}
            </p>
          )}
          {(action.steps || []).map((s, i) => (
            <div key={i} style={{ display: "flex", gap: 8, fontSize: 11,
                                   color: "var(--text-secondary)", marginBottom: 4,
                                   alignItems: "flex-start" }}>
              <span style={{ color: c.text, fontFamily: "var(--font-mono)", flexShrink: 0 }}>
                {(i + 1).toString().padStart(2,"0")}
              </span>
              <span>{s.action}</span>
              {s.file && (
                <code style={{ background: "rgba(0,0,0,0.4)", padding: "0 5px",
                                borderRadius: 3, fontSize: 10, color: "var(--blue)",
                                fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}>
                  {s.file}
                </code>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function OpusPanel() {
  const { data }        = useOpusMemory();
  const evals           = data?.evaluations || [];
  const [sel, setSel]   = useState(0);

  if (!evals.length) {
    return (
      <Card>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border-dim)" }}>
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.12em",
                          textTransform: "uppercase", color: "var(--text-muted)",
                          fontFamily: "var(--font-mono)" }}>OPUS EVALUATION</span>
        </div>
        <Empty text="No evaluations yet — runs every Monday 08:00 WIB" />
      </Card>
    );
  }

  const ev      = evals[sel] || {};
  const summary = ev.summary || {};
  const actions = ev.actions_required || [];
  const updated = ev.params_updated   || {};
  const p0 = actions.filter(a => a.priority === "P0");
  const p1 = actions.filter(a => a.priority === "P1");
  const p2 = actions.filter(a => a.priority === "P2");

  return (
    <Card>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "12px 16px", borderBottom: "1px solid var(--border-dim)",
      }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.12em",
                        textTransform: "uppercase", color: "var(--text-muted)",
                        fontFamily: "var(--font-mono)" }}>OPUS EVALUATION</span>
        {/* Week selector */}
        <div style={{ display: "flex", gap: 3 }}>
          {evals.map((e, i) => (
            <button key={i} onClick={() => setSel(i)} style={{
              padding: "2px 7px", borderRadius: 4, border: "none",
              background: sel === i ? "var(--accent-dim)" : "transparent",
              color: sel === i ? "var(--accent)" : "var(--text-muted)",
              fontSize: 9, fontWeight: 700, cursor: "pointer",
              fontFamily: "var(--font-mono)",
            }}>
              {(e.week_start || "").slice(5)}
            </button>
          ))}
        </div>
      </div>

      <div style={{ padding: "12px 16px" }}>
        {/* Metrics row */}
        <div style={{
          display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8, marginBottom: 14,
        }}>
          {[
            { l: "WIN",    v: (parseFloat(summary.win_rate || 0)*100).toFixed(1)+"%",
              c: summary.win_rate >= 0.6 ? "var(--green)" : summary.win_rate >= 0.5 ? "var(--yellow)" : "var(--red)" },
            { l: "PnL",    v: "$"+parseFloat(summary.total_pnl || 0).toFixed(2),
              c: summary.total_pnl >= 0 ? "var(--green)" : "var(--red)" },
            { l: "SHARPE", v: parseFloat(summary.sharpe_ratio || 0).toFixed(2),
              c: summary.sharpe_ratio >= 1 ? "var(--green)" : "var(--yellow)" },
            { l: "MAX DD", v: (parseFloat(summary.max_drawdown || 0)*100).toFixed(1)+"%",
              c: summary.max_drawdown > 0.1 ? "var(--red)" : "var(--green)" },
            { l: "TRADES", v: summary.total_trades || 0, c: "var(--text-primary)" },
          ].map(({ l, v, c }) => (
            <div key={l} style={{
              background: "var(--bg-elevated)", borderRadius: "var(--radius-sm)",
              padding: "8px 10px", textAlign: "center",
            }}>
              <div style={{ fontSize: 8, color: "var(--text-muted)", fontFamily: "var(--font-mono)",
                            letterSpacing: "0.1em", marginBottom: 4 }}>{l}</div>
              <div style={{ fontSize: 15, fontWeight: 700, fontFamily: "var(--font-mono)", color: c }}>{v}</div>
            </div>
          ))}
        </div>

        {/* Assessment */}
        {summary.assessment && (
          <div style={{
            padding: "10px 12px", borderRadius: "var(--radius-md)",
            background: "rgba(0,212,170,0.05)", border: "1px solid rgba(0,212,170,0.12)",
            fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6, marginBottom: 12,
            fontStyle: "italic",
          }}>
            {summary.assessment}
          </div>
        )}

        {/* Actions */}
        {actions.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 9, fontWeight: 700, color: "var(--text-muted)",
                          fontFamily: "var(--font-mono)", letterSpacing: "0.1em",
                          marginBottom: 8 }}>
              ACTION REQUIRED ({actions.length})
            </div>
            {[...p0, ...p1, ...p2].map((a, i) => <ActionCard key={i} action={a} />)}
          </div>
        )}

        {/* Auto-updated params */}
        {Object.keys(updated).length > 0 && (
          <div>
            <div style={{ fontSize: 9, fontWeight: 700, color: "var(--text-muted)",
                          fontFamily: "var(--font-mono)", letterSpacing: "0.1em", marginBottom: 8 }}>
              AUTO-UPDATED PARAMS
            </div>
            {Object.entries(updated).map(([pair, params]) => (
              <div key={pair} style={{
                background: "var(--bg-elevated)", borderRadius: "var(--radius-sm)",
                padding: "8px 10px", marginBottom: 6,
              }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: "var(--accent)",
                              fontFamily: "var(--font-mono)", marginBottom: 5 }}>{pair}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {Object.entries(params).map(([k, v]) => (
                    <span key={k} style={{
                      fontSize: 10, fontFamily: "var(--font-mono)",
                      color: "var(--text-secondary)",
                    }}>
                      <span style={{ color: "var(--text-muted)" }}>{k}:</span>{" "}
                      <span style={{ color: "var(--green)" }}>{JSON.stringify(v)}</span>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Cost */}
        <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)",
                      fontFamily: "var(--font-mono)", display: "flex", gap: 10 }}>
          <span>Cost: <span style={{ color: "var(--text-secondary)" }}>
            ${parseFloat(ev.token_cost || 0).toFixed(4)}
          </span></span>
          <span>Period: <span style={{ color: "var(--text-secondary)" }}>
            {ev.week_start} → {ev.week_end}
          </span></span>
        </div>
      </div>
    </Card>
  );
}
