import { useState } from "react";

/* ── Status dot ─────────────────────────────────────── */
export function StatusDot({ status = "ok" }) {
  const colors = { ok: "#00d4aa", paused: "#ffd32a", stopping: "#ff4757", degraded: "#ff4757", running: "#00d4aa" };
  return (
    <span style={{
      display: "inline-block", width: 7, height: 7, borderRadius: "50%",
      background: colors[status] || "#8b98a8",
      boxShadow: `0 0 6px ${colors[status] || "#8b98a8"}`,
      animation: status === "ok" || status === "running" ? "pulse-dot 2s ease-in-out infinite" : "none",
      flexShrink: 0,
    }} />
  );
}

/* ── Badge ───────────────────────────────────────────── */
export function Badge({ children, color = "default", size = "sm" }) {
  const colors = {
    green:   { bg: "var(--green-dim)",  text: "var(--green)"  },
    red:     { bg: "var(--red-dim)",    text: "var(--red)"    },
    yellow:  { bg: "var(--yellow-dim)", text: "var(--yellow)" },
    blue:    { bg: "var(--blue-dim)",   text: "var(--blue)"   },
    purple:  { bg: "var(--purple-dim)", text: "var(--purple)" },
    default: { bg: "rgba(255,255,255,0.06)", text: "var(--text-secondary)" },
  };
  const c = colors[color] || colors.default;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center",
      padding: size === "xs" ? "1px 5px" : "2px 8px",
      borderRadius: 20,
      fontSize: size === "xs" ? 9 : 10,
      fontWeight: 600,
      letterSpacing: "0.05em",
      textTransform: "uppercase",
      background: c.bg, color: c.text,
      fontFamily: "var(--font-mono)",
      whiteSpace: "nowrap",
    }}>
      {children}
    </span>
  );
}

/* ── Card ────────────────────────────────────────────── */
export function Card({ children, style, className }) {
  return (
    <div className={className} style={{
      background: "var(--bg-surface)",
      border: "1px solid var(--border-soft)",
      borderRadius: "var(--radius-lg)",
      ...style,
    }}>
      {children}
    </div>
  );
}

/* ── Section title ───────────────────────────────────── */
export function SectionTitle({ children, right }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "12px 16px",
      borderBottom: "1px solid var(--border-dim)",
    }}>
      <span style={{
        fontSize: 10, fontWeight: 700, letterSpacing: "0.12em",
        textTransform: "uppercase", color: "var(--text-muted)",
        fontFamily: "var(--font-mono)",
      }}>
        {children}
      </span>
      {right && <div>{right}</div>}
    </div>
  );
}

/* ── Metric row ──────────────────────────────────────── */
export function MetricRow({ label, value, color, mono = true }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "7px 16px",
      borderBottom: "1px solid var(--border-dim)",
    }}>
      <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{label}</span>
      <span style={{
        fontSize: 12, fontWeight: 600,
        color: color || "var(--text-primary)",
        fontFamily: mono ? "var(--font-mono)" : "var(--font-ui)",
      }}>
        {value}
      </span>
    </div>
  );
}

/* ── Big metric card ─────────────────────────────────── */
export function BigMetric({ label, value, sub, color = "var(--text-primary)", icon }) {
  return (
    <div style={{
      background: "var(--bg-surface)",
      border: "1px solid var(--border-soft)",
      borderRadius: "var(--radius-lg)",
      padding: "14px 16px",
    }}>
      <div style={{ fontSize: 10, color: "var(--text-muted)", letterSpacing: "0.1em",
                    textTransform: "uppercase", fontFamily: "var(--font-mono)", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color, fontFamily: "var(--font-mono)",
                    letterSpacing: "-0.02em", lineHeight: 1 }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>{sub}</div>
      )}
    </div>
  );
}

/* ── Tabs ────────────────────────────────────────────── */
export function Tabs({ tabs, active, onChange }) {
  return (
    <div style={{ display: "flex", gap: 2, padding: "8px 16px 0" }}>
      {tabs.map(tab => (
        <button key={tab.key} onClick={() => onChange(tab.key)} style={{
          padding: "5px 12px",
          borderRadius: "var(--radius-sm) var(--radius-sm) 0 0",
          border: "none",
          background: active === tab.key ? "var(--bg-elevated)" : "transparent",
          color: active === tab.key ? "var(--text-primary)" : "var(--text-muted)",
          fontSize: 11, fontWeight: 600, cursor: "pointer",
          fontFamily: "var(--font-mono)",
          letterSpacing: "0.04em",
          transition: "all 0.15s",
        }}>
          {tab.label}
        </button>
      ))}
    </div>
  );
}

/* ── Loading skeleton ────────────────────────────────── */
export function Skeleton({ h = 16, w = "100%", style }) {
  return (
    <div style={{
      height: h, width: w,
      background: "linear-gradient(90deg, var(--bg-elevated) 25%, var(--bg-hover) 50%, var(--bg-elevated) 75%)",
      backgroundSize: "200% 100%",
      borderRadius: "var(--radius-sm)",
      animation: "shimmer 1.5s infinite",
      ...style,
    }} />
  );
}

/* ── Empty state ─────────────────────────────────────── */
export function Empty({ text = "No data" }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "center",
      padding: "32px 16px",
      color: "var(--text-dim)", fontSize: 12,
      fontFamily: "var(--font-mono)",
    }}>
      {text}
    </div>
  );
}

/* ── PnL display ─────────────────────────────────────── */
export function PnL({ value, size = 13 }) {
  const v = parseFloat(value || 0);
  return (
    <span style={{
      fontSize: size, fontWeight: 600,
      color: v > 0 ? "var(--green)" : v < 0 ? "var(--red)" : "var(--text-muted)",
      fontFamily: "var(--font-mono)",
    }}>
      {v > 0 ? "+" : ""}{v.toFixed(2)}
    </span>
  );
}
