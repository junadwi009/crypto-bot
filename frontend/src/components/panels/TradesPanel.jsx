import { useState } from "react";
import { Card, SectionTitle, Badge, PnL, Empty, Tabs } from "../ui/index.jsx";
import { useOpenTrades, useRecentTrades } from "../../hooks/useData.js";

function TradeRow({ trade, isOpen }) {
  const pnl    = parseFloat(trade.pnl_usd || 0);
  const entry  = parseFloat(trade.entry_price || 0);
  const exit   = parseFloat(trade.exit_price  || 0);
  const size   = parseFloat(trade.amount_usd  || 0);
  const time   = trade.opened_at
    ? new Date(trade.opened_at).toLocaleString("id-ID", { month:"short", day:"numeric", hour:"2-digit", minute:"2-digit" })
    : "—";

  const sourceColor = {
    rule_based: "#4fc3f7",
    haiku:      "#00d4aa",
    sonnet:     "#a78bfa",
    news:       "#ffd32a",
  }[trade.trigger_source] || "#8b98a8";

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "70px 42px 70px 80px 80px 80px 70px 1fr",
      gap: 4,
      padding: "7px 16px",
      borderBottom: "1px solid var(--border-dim)",
      fontSize: 11,
      fontFamily: "var(--font-mono)",
      alignItems: "center",
      transition: "background 0.1s",
    }}
    onMouseEnter={e => e.currentTarget.style.background = "var(--bg-elevated)"}
    onMouseLeave={e => e.currentTarget.style.background = "transparent"}
    >
      <span style={{ fontWeight: 700 }}>{trade.pair}</span>
      <span style={{ color: trade.side === "buy" ? "var(--green)" : "var(--red)", fontWeight: 700 }}>
        {trade.side?.toUpperCase()}
      </span>
      <span style={{ color: "var(--text-secondary)" }}>${size.toFixed(2)}</span>
      <span>{entry.toLocaleString()}</span>
      <span style={{ color: "var(--text-muted)" }}>{exit ? exit.toLocaleString() : "open"}</span>
      <span>{isOpen ? <span style={{ color: "var(--yellow)" }}>OPEN</span> : <PnL value={pnl} />}</span>
      <span style={{ color: sourceColor, fontSize: 10 }}>{trade.trigger_source || "—"}</span>
      <span style={{ color: "var(--text-muted)", fontSize: 10 }}>{time}</span>
    </div>
  );
}

const TABLE_HEADER = ["PAIR","SIDE","SIZE","ENTRY","EXIT","PnL","SOURCE","TIME"];

export default function TradesPanel() {
  const [tab,  setTab]  = useState("open");
  const [days, setDays] = useState(7);
  const { data: openData   } = useOpenTrades();
  const { data: recentData } = useRecentTrades(days);

  const open   = openData?.trades   || [];
  const closed = recentData?.trades || [];
  const wins   = closed.filter(t => parseFloat(t.pnl_usd || 0) > 0);
  const wr     = closed.length ? (wins.length / closed.length * 100).toFixed(0) : "—";
  const netPnl = closed.reduce((s, t) => s + parseFloat(t.pnl_usd || 0), 0);

  return (
    <Card style={{ overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "12px 16px", borderBottom: "1px solid var(--border-dim)",
      }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.12em",
                          textTransform: "uppercase", color: "var(--text-muted)",
                          fontFamily: "var(--font-mono)" }}>TRADES</span>
          <Badge color={open.length > 0 ? "green" : "default"}>{open.length} open</Badge>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          <button onClick={() => setTab("open")} style={{
            padding: "3px 10px", borderRadius: 4, border: "none",
            background: tab === "open" ? "var(--accent-dim)" : "transparent",
            color: tab === "open" ? "var(--accent)" : "var(--text-muted)",
            fontSize: 10, fontWeight: 600, cursor: "pointer", fontFamily: "var(--font-mono)",
          }}>OPEN</button>
          <button onClick={() => setTab("history")} style={{
            padding: "3px 10px", borderRadius: 4, border: "none",
            background: tab === "history" ? "var(--accent-dim)" : "transparent",
            color: tab === "history" ? "var(--accent)" : "var(--text-muted)",
            fontSize: 10, fontWeight: 600, cursor: "pointer", fontFamily: "var(--font-mono)",
          }}>HISTORY</button>
        </div>
      </div>

      {/* Stats row for history */}
      {tab === "history" && (
        <div style={{
          display: "flex", gap: 0,
          borderBottom: "1px solid var(--border-dim)",
        }}>
          {[
            { l: "CLOSED",   v: closed.length },
            { l: "WIN RATE", v: wr + "%", c: parseFloat(wr) >= 60 ? "var(--green)" : parseFloat(wr) >= 50 ? "var(--yellow)" : "var(--red)" },
            { l: "NET PnL",  v: (netPnl >= 0 ? "+" : "") + "$" + netPnl.toFixed(2), c: netPnl >= 0 ? "var(--green)" : "var(--red)" },
            { l: "WINNERS",  v: wins.length },
          ].map(({ l, v, c }) => (
            <div key={l} style={{ flex: 1, padding: "8px 16px", borderRight: "1px solid var(--border-dim)" }}>
              <div style={{ fontSize: 9, color: "var(--text-muted)", fontFamily: "var(--font-mono)",
                            letterSpacing: "0.1em", marginBottom: 3 }}>{l}</div>
              <div style={{ fontSize: 14, fontWeight: 700, fontFamily: "var(--font-mono)",
                            color: c || "var(--text-primary)" }}>{v}</div>
            </div>
          ))}
          <div style={{ display: "flex", alignItems: "center", padding: "0 12px", gap: 4 }}>
            {[3,7,14].map(d => (
              <button key={d} onClick={() => setDays(d)} style={{
                padding: "2px 6px", borderRadius: 4, border: "none",
                background: days === d ? "var(--accent-dim)" : "transparent",
                color: days === d ? "var(--accent)" : "var(--text-muted)",
                fontSize: 9, fontWeight: 700, cursor: "pointer", fontFamily: "var(--font-mono)",
              }}>{d}D</button>
            ))}
          </div>
        </div>
      )}

      {/* Table header */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "70px 42px 70px 80px 80px 80px 70px 1fr",
        gap: 4,
        padding: "6px 16px",
        borderBottom: "1px solid var(--border-dim)",
      }}>
        {TABLE_HEADER.map(h => (
          <span key={h} style={{
            fontSize: 9, fontWeight: 700, letterSpacing: "0.08em",
            color: "var(--text-muted)", fontFamily: "var(--font-mono)",
          }}>{h}</span>
        ))}
      </div>

      {/* Rows */}
      <div style={{ maxHeight: 280, overflowY: "auto" }}>
        {tab === "open" && (
          open.length === 0
            ? <Empty text="No open positions" />
            : open.map((t, i) => <TradeRow key={i} trade={t} isOpen />)
        )}
        {tab === "history" && (
          closed.length === 0
            ? <Empty text="No closed trades in this period" />
            : closed.map((t, i) => <TradeRow key={i} trade={t} isOpen={false} />)
        )}
      </div>
    </Card>
  );
}
