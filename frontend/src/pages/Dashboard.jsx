import { useState, useEffect } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine
} from "recharts";
import MetricCard from "../components/MetricCard.jsx";
import TradeTable from "../components/TradeTable.jsx";
import { api }    from "../api.js";

const PAGE = { title: "Dashboard", desc: "Ringkasan performa real-time" };

export default function Dashboard() {
  const [status,  setStatus]  = useState(null);
  const [summary, setSummary] = useState(null);
  const [history, setHistory] = useState([]);
  const [open,    setOpen]    = useState([]);
  const [recent,  setRecent]  = useState([]);
  const [claude,  setClaude]  = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const [st, sm, hi, op, re, cl] = await Promise.allSettled([
          api.status(),
          api.portfolioSummary(),
          api.portfolioHistory(14),
          api.openTrades(),
          api.recentTrades(3),
          api.claudeUsage(),
        ]);
        if (st.status === "fulfilled") setStatus(st.value);
        if (sm.status === "fulfilled") setSummary(sm.value);
        if (hi.status === "fulfilled") {
          const data = (hi.value?.data ?? [])
            .slice(0, 14)
            .reverse()
            .map(d => ({
              date:    d.snapshot_date?.slice(5),   // MM-DD
              capital: parseFloat(d.total_capital ?? 0),
              pnl:     parseFloat(d.daily_pnl ?? 0),
            }));
          setHistory(data);
        }
        if (op.status === "fulfilled") setOpen(op.value?.trades ?? []);
        if (re.status === "fulfilled") setRecent(re.value?.trades ?? []);
        if (cl.status === "fulfilled") setClaude(cl.value);
      } finally {
        setLoading(false);
      }
    };
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);

  if (loading) return <LoadingState />;

  const capital   = parseFloat(status?.capital ?? 0);
  const dailyPnl  = parseFloat(summary?.daily_pnl ?? summary?.net_pnl ?? 0);
  const winRate   = parseFloat(summary?.win_rate ?? 0);
  const drawdown  = parseFloat(summary?.max_drawdown ?? 0);
  const pnlColor  = dailyPnl >= 0 ? "green" : "red";
  const ddColor   = drawdown > 0.10 ? "red" : drawdown > 0.05 ? "yellow" : "green";
  const wrColor   = winRate >= 0.60 ? "green" : winRate >= 0.50 ? "yellow" : "red";

  const recent5 = (recent ?? []).slice(0, 5);

  return (
    <div>
      <PageHeader {...PAGE} />

      {/* Metric cards */}
      <div style={s.grid4}>
        <MetricCard
          label="Modal sekarang"
          value={`$${capital.toFixed(2)}`}
          sub={status?.tier?.toUpperCase() + " tier"}
          color="blue"
        />
        <MetricCard
          label="PnL hari ini"
          value={`${dailyPnl >= 0 ? "+" : ""}$${dailyPnl.toFixed(2)}`}
          sub={`${summary?.total_trades ?? 0} trades`}
          color={pnlColor}
        />
        <MetricCard
          label="Win rate 7d"
          value={`${(winRate * 100).toFixed(1)}%`}
          sub="closed trades"
          color={wrColor}
        />
        <MetricCard
          label="Max drawdown 7d"
          value={`${(drawdown * 100).toFixed(1)}%`}
          sub="limit 15%"
          color={ddColor}
        />
      </div>

      {/* Capital chart */}
      {history.length > 0 && (
        <Section title="Modal 14 hari">
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={history} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="capGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}    />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" tick={axTick} axisLine={false} tickLine={false} />
              <YAxis tick={axTick} axisLine={false} tickLine={false} width={56}
                     tickFormatter={v => `$${v.toFixed(0)}`} />
              <Tooltip contentStyle={ttStyle}
                       formatter={v => [`$${v.toFixed(2)}`, "Modal"]} />
              <Area type="monotone" dataKey="capital"
                    stroke="#3b82f6" strokeWidth={2}
                    fill="url(#capGrad)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </Section>
      )}

      <div style={s.grid2}>
        {/* Open positions */}
        <Section title={`Open positions (${open.length})`}>
          {open.length === 0
            ? <Empty text="Tidak ada posisi terbuka." />
            : <TradeTable trades={open} showStatus={false} />
          }
        </Section>

        {/* Claude usage */}
        {claude && (
          <Section title="Claude budget">
            <div style={s.claudeRows}>
              <ClaudeRow label="Kredit estimasi"
                         value={`$${parseFloat(claude.estimated_balance ?? 0).toFixed(2)}`}
                         warn={claude.estimated_balance < 10} />
              <ClaudeRow label="Burn rate"
                         value={`$${parseFloat(claude.burn_rate_per_day ?? 0).toFixed(2)}/hari`} />
              <ClaudeRow label="Estimasi sisa"
                         value={`${parseFloat(claude.days_remaining ?? 0).toFixed(1)} hari`}
                         warn={claude.days_remaining < 5} />
              <ClaudeRow label="Biaya bulan ini"
                         value={`$${parseFloat(claude.monthly_cost_usd ?? 0).toFixed(2)}`} />
              <ClaudeRow label="Mode Claude"
                         value={claude.mode?.toUpperCase()}
                         warn={claude.mode !== "normal"} />
            </div>
          </Section>
        )}
      </div>

      {/* Recent trades */}
      <Section title="Trade terbaru (3 hari)">
        {recent5.length === 0
          ? <Empty text="Belum ada trade." />
          : <TradeTable trades={recent5} showStatus />
        }
      </Section>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function PageHeader({ title, desc }) {
  return (
    <div style={s.pageHeader}>
      <h1 style={s.pageTitle}>{title}</h1>
      <p style={s.pageDesc}>{desc}</p>
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

function ClaudeRow({ label, value, warn = false }) {
  return (
    <div style={s.claudeRow}>
      <span style={s.claudeLabel}>{label}</span>
      <span style={{ ...s.claudeVal, color: warn ? "#fbbf24" : "#e2e8f0" }}>
        {value}
      </span>
    </div>
  );
}

function Empty({ text }) {
  return <div style={s.empty}>{text}</div>;
}

function LoadingState() {
  return (
    <div style={s.loading}>
      <div style={s.loadingDot} />
      <span>Memuat data...</span>
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const axTick  = { fontSize: 10, fill: "#64748b" };
const ttStyle = {
  background: "#161b27", border: "1px solid #1e2535",
  borderRadius: 8, fontSize: 12, color: "#e2e8f0",
};

const s = {
  pageHeader: { marginBottom: 24 },
  pageTitle:  { fontSize: 20, fontWeight: 700, color: "#e2e8f0", marginBottom: 4 },
  pageDesc:   { fontSize: 13, color: "#64748b" },
  grid4: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
    gap: 12,
    marginBottom: 20,
  },
  grid2: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: 20,
    marginBottom: 20,
  },
  section: {
    background: "#161b27",
    border: "1px solid #1e2535",
    borderRadius: 10,
    padding: "14px 16px",
    marginBottom: 20,
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: 600,
    color: "#94a3b8",
    letterSpacing: "0.05em",
    textTransform: "uppercase",
    marginBottom: 12,
  },
  claudeRows: { display: "flex", flexDirection: "column", gap: 10 },
  claudeRow:  { display: "flex", justifyContent: "space-between", alignItems: "center" },
  claudeLabel:{ fontSize: 12, color: "#64748b" },
  claudeVal:  { fontSize: 13, fontWeight: 600 },
  empty:      { fontSize: 12, color: "#475569", padding: "8px 0" },
  loading: {
    display: "flex", alignItems: "center", gap: 10,
    color: "#64748b", fontSize: 13, padding: "40px 0",
  },
  loadingDot: {
    width: 8, height: 8, borderRadius: "50%",
    background: "#3b82f6",
    animation: "pulse 1.2s ease-in-out infinite",
  },
};
