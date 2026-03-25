import { Card, Badge, Empty } from "../ui/index.jsx";
import { usePairs } from "../../hooks/useData.js";

function ScoreBar({ value, max = 1 }) {
  const pct   = Math.min(Math.max(value / max, 0), 1) * 100;
  const color = value >= 0.65 ? "var(--green)"
              : value >= 0.45 ? "var(--yellow)" : "var(--red)";
  return (
    <div style={{ height: 3, background: "var(--border-dim)", borderRadius: 2, marginTop: 6 }}>
      <div style={{ height: "100%", width: pct + "%", background: color,
                    borderRadius: 2, transition: "width 0.4s" }} />
    </div>
  );
}

function PairCard({ pair }) {
  const score   = parseFloat(pair.lrhr_score   || 0);
  const winRate = parseFloat(pair.win_rate_30d  || 0);
  const maxAlloc= pair.max_allocation_pct || 5;
  const isActive= pair.active;

  const strategyColor = {
    rsi_momentum:   "#00d4aa",
    macd_crossover: "#a78bfa",
    bb_reversion:   "#ffd32a",
  }[pair.strategy] || "#8b98a8";

  return (
    <div style={{
      background: isActive ? "var(--bg-elevated)" : "var(--bg-base)",
      border: `1px solid ${isActive ? "var(--border-mid)" : "var(--border-dim)"}`,
      borderRadius: "var(--radius-md)",
      padding: "12px 14px",
      opacity: isActive ? 1 : 0.55,
      transition: "all 0.15s",
    }}
    onMouseEnter={e => e.currentTarget.style.borderColor = isActive ? "var(--accent)" : "var(--border-soft)"}
    onMouseLeave={e => e.currentTarget.style.borderColor = isActive ? "var(--border-mid)" : "var(--border-dim)"}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, fontFamily: "var(--font-mono)",
                        color: isActive ? "var(--text-primary)" : "var(--text-secondary)" }}>
            {pair.pair}
          </div>
          <div style={{ fontSize: 9, color: strategyColor, marginTop: 2,
                        fontFamily: "var(--font-mono)", letterSpacing: "0.04em" }}>
            {(pair.strategy || "").replace(/_/g, " ").toUpperCase()}
          </div>
        </div>
        <Badge color={isActive ? "green" : "default"} size="xs">
          {isActive ? "ON" : "OFF"}
        </Badge>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
        {[
          { l: "LRHR",  v: (score * 100).toFixed(0),       c: score >= 0.65 ? "var(--green)" : score >= 0.45 ? "var(--yellow)" : "var(--red)" },
          { l: "WIN%",  v: (winRate * 100).toFixed(0) + "%", c: winRate >= 0.6 ? "var(--green)" : "var(--text-secondary)" },
          { l: "ALLOC", v: maxAlloc + "%",                  c: "var(--text-secondary)" },
        ].map(({ l, v, c }) => (
          <div key={l}>
            <div style={{ fontSize: 8, color: "var(--text-muted)", fontFamily: "var(--font-mono)",
                          letterSpacing: "0.1em", marginBottom: 2 }}>{l}</div>
            <div style={{ fontSize: 14, fontWeight: 700, fontFamily: "var(--font-mono)", color: c }}>{v}</div>
          </div>
        ))}
      </div>

      <ScoreBar value={score} />

      {!isActive && pair.inactive_reason && (
        <div style={{ marginTop: 8, fontSize: 9, color: "var(--yellow)",
                      fontFamily: "var(--font-mono)", opacity: 0.8 }}>
          {pair.inactive_reason}
        </div>
      )}
    </div>
  );
}

export default function PairsPanel() {
  const { data } = usePairs();
  const pairs    = data?.pairs || [];
  const active   = pairs.filter(p => p.active);
  const inactive = pairs.filter(p => !p.active);

  return (
    <Card>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        padding: "12px 16px", borderBottom: "1px solid var(--border-dim)",
      }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.12em",
                        textTransform: "uppercase", color: "var(--text-muted)",
                        fontFamily: "var(--font-mono)" }}>PAIRS</span>
        <div style={{ display: "flex", gap: 6 }}>
          <Badge color="green">{active.length} active</Badge>
          <Badge>{inactive.length} pending</Badge>
        </div>
      </div>

      <div style={{ padding: 12 }}>
        {pairs.length === 0
          ? <Empty text="No pairs configured" />
          : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 8 }}>
              {[...active, ...inactive].map(p => <PairCard key={p.pair} pair={p} />)}
            </div>
          )
        }
      </div>
    </Card>
  );
}
