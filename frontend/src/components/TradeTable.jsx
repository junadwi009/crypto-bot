/**
 * TradeTable — tabel trade dengan PnL coloring.
 * Props: trades (array), showStatus (bool)
 */
export default function TradeTable({ trades = [], showStatus = false }) {
  if (!trades.length) {
    return <div style={s.empty}>Tidak ada data trade.</div>;
  }

  return (
    <div style={s.wrap}>
      <table style={s.table}>
        <thead>
          <tr>
            <th style={s.th}>Pair</th>
            <th style={s.th}>Side</th>
            <th style={s.th}>Size</th>
            <th style={s.th}>Entry</th>
            <th style={s.th}>Exit</th>
            <th style={s.th}>PnL</th>
            <th style={s.th}>Source</th>
            {showStatus && <th style={s.th}>Status</th>}
            <th style={s.th}>Waktu</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => {
            const pnl    = parseFloat(t.pnl_usd ?? 0);
            const isWin  = pnl > 0;
            const isOpen = t.status === "open";
            const pnlColor = isOpen ? "#94a3b8"
                           : isWin  ? "#4ade80" : "#f87171";

            return (
              <tr key={t.id ?? i} style={i % 2 === 0 ? s.rowEven : s.rowOdd}>
                <td style={s.td}>{t.pair}</td>
                <td style={{ ...s.td, color: t.side === "buy" ? "#4ade80" : "#f87171",
                             fontWeight: 600 }}>
                  {t.side?.toUpperCase()}
                </td>
                <td style={s.td}>${parseFloat(t.amount_usd ?? 0).toFixed(2)}</td>
                <td style={{ ...s.td, ...s.mono }}>
                  {parseFloat(t.entry_price ?? 0).toLocaleString()}
                </td>
                <td style={{ ...s.td, ...s.mono }}>
                  {t.exit_price
                    ? parseFloat(t.exit_price).toLocaleString()
                    : "—"}
                </td>
                <td style={{ ...s.td, color: pnlColor, fontWeight: 600 }}>
                  {isOpen ? "open"
                           : `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}`}
                </td>
                <td style={{ ...s.td, ...s.badge }}>
                  <span style={s.sourceBadge}>
                    {t.trigger_source ?? "—"}
                  </span>
                </td>
                {showStatus && (
                  <td style={s.td}>
                    <span style={{
                      ...s.sourceBadge,
                      background: t.status === "open" ? "#1e3a5f" : "#1a2e1a",
                      color:      t.status === "open" ? "#60a5fa" : "#4ade80",
                    }}>
                      {t.status}
                    </span>
                  </td>
                )}
                <td style={{ ...s.td, color: "#64748b", fontSize: 11 }}>
                  {t.opened_at
                    ? new Date(t.opened_at).toLocaleString("id-ID", {
                        month: "short", day: "numeric",
                        hour: "2-digit", minute: "2-digit",
                      })
                    : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const s = {
  wrap: { overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", fontSize: 13 },
  th: {
    padding: "8px 12px",
    textAlign: "left",
    fontSize: 11,
    color: "#64748b",
    letterSpacing: "0.05em",
    textTransform: "uppercase",
    borderBottom: "1px solid #1e2535",
    whiteSpace: "nowrap",
  },
  td: { padding: "9px 12px", color: "#cbd5e1", whiteSpace: "nowrap" },
  rowEven: { background: "transparent" },
  rowOdd:  { background: "#0d111a" },
  mono: { fontFamily: "monospace", fontSize: 12 },
  badge: { fontSize: 11 },
  sourceBadge: {
    display: "inline-block",
    padding: "2px 7px",
    borderRadius: 10,
    fontSize: 10,
    fontWeight: 500,
    background: "#1e2535",
    color: "#94a3b8",
  },
  empty: { color: "#475569", fontSize: 13, padding: "20px 0" },
};
