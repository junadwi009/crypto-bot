/**
 * PairCard — kartu status satu pair trading.
 * Props: pair (object dari /api/pairs)
 */
export default function PairCard({ pair }) {
  const score    = parseFloat(pair.lrhr_score ?? 0);
  const winRate  = parseFloat(pair.win_rate_30d ?? 0);
  const scoreColor = score >= 0.65 ? "#4ade80"
                   : score >= 0.45 ? "#fbbf24" : "#f87171";

  return (
    <div style={{ ...s.card, ...(pair.active ? s.cardActive : s.cardInactive) }}>
      <div style={s.header}>
        <div>
          <div style={s.name}>{pair.pair}</div>
          <div style={s.cat}>{pair.category}</div>
        </div>
        <span style={{ ...s.badge, ...(pair.active ? s.badgeActive : s.badgeOff) }}>
          {pair.active ? "Aktif" : "Off"}
        </span>
      </div>

      <div style={s.metrics}>
        <div style={s.metric}>
          <div style={s.metricLabel}>LRHR score</div>
          <div style={{ ...s.metricValue, color: scoreColor }}>
            {(score * 100).toFixed(0)}
          </div>
        </div>
        <div style={s.metric}>
          <div style={s.metricLabel}>Win rate 30d</div>
          <div style={s.metricValue}>{(winRate * 100).toFixed(0)}%</div>
        </div>
        <div style={s.metric}>
          <div style={s.metricLabel}>Max alloc</div>
          <div style={s.metricValue}>{pair.max_allocation_pct}%</div>
        </div>
      </div>

      {/* Score bar */}
      <div style={s.barTrack}>
        <div style={{ ...s.barFill, width: `${score * 100}%`, background: scoreColor }} />
      </div>

      <div style={s.strategy}>{pair.strategy ?? "—"}</div>

      {!pair.active && pair.inactive_reason && (
        <div style={s.inactiveReason}>{pair.inactive_reason}</div>
      )}
    </div>
  );
}

const s = {
  card: {
    borderRadius: 10,
    padding: "14px 16px",
    border: "1px solid #1e2535",
  },
  cardActive:   { background: "#161b27" },
  cardInactive: { background: "#0d111a", opacity: 0.6 },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    marginBottom: 12,
  },
  name:  { fontSize: 14, fontWeight: 700, color: "#e2e8f0" },
  cat:   { fontSize: 10, color: "#64748b", marginTop: 2 },
  badge: {
    fontSize: 10,
    fontWeight: 600,
    padding: "2px 8px",
    borderRadius: 10,
    letterSpacing: "0.04em",
  },
  badgeActive: { background: "#14532d", color: "#4ade80" },
  badgeOff:    { background: "#1e2535", color: "#64748b" },
  metrics: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr 1fr",
    gap: 8,
    marginBottom: 10,
  },
  metric:      { textAlign: "center" },
  metricLabel: { fontSize: 9, color: "#64748b", textTransform: "uppercase",
                 letterSpacing: "0.05em", marginBottom: 3 },
  metricValue: { fontSize: 16, fontWeight: 700, color: "#e2e8f0" },
  barTrack: {
    height: 3,
    background: "#1e2535",
    borderRadius: 2,
    overflow: "hidden",
    marginBottom: 8,
  },
  barFill: { height: "100%", borderRadius: 2, transition: "width 0.3s" },
  strategy: { fontSize: 10, color: "#475569" },
  inactiveReason: {
    marginTop: 6,
    fontSize: 10,
    color: "#854d0e",
    background: "#422006",
    padding: "3px 7px",
    borderRadius: 5,
  },
};
