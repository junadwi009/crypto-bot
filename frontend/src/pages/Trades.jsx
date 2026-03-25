import { useState, useEffect } from "react";
import TradeTable from "../components/TradeTable.jsx";
import MetricCard from "../components/MetricCard.jsx";
import { api }    from "../api.js";

export default function Trades() {
  const [open,   setOpen]   = useState([]);
  const [recent, setRecent] = useState([]);
  const [days,   setDays]   = useState(7);
  const [loading,setLoading]= useState(true);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const [op, re] = await Promise.all([
          api.openTrades(),
          api.recentTrades(days),
        ]);
        setOpen(op?.trades ?? []);
        setRecent(re?.trades ?? []);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [days]);

  const closed  = recent.filter(t => t.status === "closed");
  const winners = closed.filter(t => (t.pnl_usd ?? 0) > 0);
  const totalPnl= closed.reduce((s, t) => s + parseFloat(t.pnl_usd ?? 0), 0);
  const totalFee= closed.reduce((s, t) => s + parseFloat(t.fee_usd ?? 0), 0);
  const winRate = closed.length ? winners.length / closed.length : 0;

  return (
    <div>
      <div style={s.header}>
        <div>
          <h1 style={s.title}>Trades</h1>
          <p style={s.desc}>Riwayat dan posisi aktif</p>
        </div>
        <select
          value={days}
          onChange={e => setDays(+e.target.value)}
          style={s.select}
        >
          {[1, 3, 7, 14, 30].map(d => (
            <option key={d} value={d}>{d} hari</option>
          ))}
        </select>
      </div>

      {/* Summary metrics */}
      <div style={s.grid}>
        <MetricCard
          label="Total closed"
          value={closed.length}
          sub={`${days} hari terakhir`}
        />
        <MetricCard
          label="Win rate"
          value={`${(winRate * 100).toFixed(1)}%`}
          sub={`${winners.length}/${closed.length} trades`}
          color={winRate >= 0.6 ? "green" : winRate >= 0.5 ? "yellow" : "red"}
        />
        <MetricCard
          label="Total PnL"
          value={`${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`}
          sub={`Fee: $${totalFee.toFixed(2)}`}
          color={totalPnl >= 0 ? "green" : "red"}
        />
        <MetricCard
          label="Net PnL"
          value={`${(totalPnl - totalFee) >= 0 ? "+" : ""}$${(totalPnl - totalFee).toFixed(2)}`}
          sub="setelah fee"
          color={(totalPnl - totalFee) >= 0 ? "green" : "red"}
        />
      </div>

      {/* Open positions */}
      <Section title={`Open positions (${open.length})`}>
        {open.length === 0
          ? <Empty text="Tidak ada posisi terbuka saat ini." />
          : <TradeTable trades={open} showStatus />
        }
      </Section>

      {/* Closed trades */}
      <Section title={`Closed trades — ${days} hari terakhir (${closed.length})`}>
        {loading
          ? <Empty text="Memuat..." />
          : closed.length === 0
          ? <Empty text="Belum ada trade closed dalam periode ini." />
          : <TradeTable trades={closed} showStatus />
        }
      </Section>
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

function Empty({ text }) {
  return <div style={s.empty}>{text}</div>;
}

const s = {
  header: { display: "flex", justifyContent: "space-between",
            alignItems: "flex-start", marginBottom: 20 },
  title:  { fontSize: 20, fontWeight: 700, color: "#e2e8f0", marginBottom: 4 },
  desc:   { fontSize: 13, color: "#64748b" },
  select: {
    background: "#161b27", border: "1px solid #1e2535",
    borderRadius: 8, color: "#e2e8f0", padding: "6px 12px",
    fontSize: 13, cursor: "pointer",
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
    gap: 12, marginBottom: 20,
  },
  section: {
    background: "#161b27", border: "1px solid #1e2535",
    borderRadius: 10, padding: "14px 16px", marginBottom: 20,
  },
  sectionTitle: {
    fontSize: 12, fontWeight: 600, color: "#94a3b8",
    letterSpacing: "0.05em", textTransform: "uppercase", marginBottom: 12,
  },
  empty: { fontSize: 12, color: "#475569", padding: "8px 0" },
};
