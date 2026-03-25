import { useState, useEffect } from "react";
import { StatusDot, Badge } from "./components/ui/index.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import PinAuth   from "./auth/PinAuth.jsx";
import { api }   from "./api.js";
import "./styles/theme.css";

const NAV = [
  { key: "dashboard", label: "Dashboard",  icon: "◈" },
  { key: "trades",    label: "Trades",     icon: "⇄" },
  { key: "portfolio", label: "Portfolio",  icon: "◉" },
  { key: "opus",      label: "Opus Log",   icon: "✦" },
];

/* ── Header ─────────────────────────────────────────────── */
function Header({ status, lastUpdate }) {
  const capital  = parseFloat(status?.capital || 213);
  const dailyPnl = parseFloat(status?.daily_pnl || 0);
  const mode     = status?.paper_trade ? "PAPER" : "LIVE";
  const botStatus= status?.status || "—";

  return (
    <header style={{
      height: "var(--header-h)",
      background: "var(--bg-deep)",
      borderBottom: "1px solid var(--border-soft)",
      display: "flex", alignItems: "center",
      padding: "0 20px", gap: 20,
      position: "sticky", top: 0, zIndex: 100,
    }}>
      {/* Brand */}
      <div style={{
        width: "var(--sidebar-w)",
        display: "flex", alignItems: "center", gap: 8, flexShrink: 0,
      }}>
        <div style={{
          width: 24, height: 24, borderRadius: 6,
          background: "linear-gradient(135deg, var(--accent), #00a882)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 12, fontWeight: 900, color: "#080c10",
        }}>C</div>
        <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: "0.04em",
                        fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
          CryptoBot
        </span>
      </div>

      {/* Status indicators */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <StatusDot status={botStatus} />
          <span style={{ fontSize: 10, fontFamily: "var(--font-mono)",
                          color: "var(--text-secondary)", letterSpacing: "0.08em" }}>
            {botStatus.toUpperCase()}
          </span>
        </div>

        <div style={{ width: 1, height: 16, background: "var(--border-soft)" }} />

        <Badge color={mode === "PAPER" ? "blue" : "red"}>{mode}</Badge>
        <Badge color="yellow">{status?.tier?.toUpperCase() || "SEED"}</Badge>

        {(status?.active_pairs || []).map(p => (
          <span key={p} style={{
            fontSize: 10, fontFamily: "var(--font-mono)", fontWeight: 700,
            color: "var(--accent)",
          }}>{p}</span>
        ))}
      </div>

      {/* Right — capital + pnl */}
      <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 9, color: "var(--text-muted)", fontFamily: "var(--font-mono)",
                        letterSpacing: "0.1em" }}>CAPITAL</div>
          <div style={{ fontSize: 16, fontWeight: 700, fontFamily: "var(--font-mono)",
                        color: "var(--text-primary)" }}>
            ${capital.toFixed(2)}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 9, color: "var(--text-muted)", fontFamily: "var(--font-mono)",
                        letterSpacing: "0.1em" }}>TODAY</div>
          <div style={{ fontSize: 14, fontWeight: 700, fontFamily: "var(--font-mono)",
                        color: dailyPnl >= 0 ? "var(--green)" : "var(--red)" }}>
            {dailyPnl >= 0 ? "+" : ""}${dailyPnl.toFixed(2)}
          </div>
        </div>
        <div style={{ fontSize: 9, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
          {lastUpdate}
        </div>
      </div>
    </header>
  );
}

/* ── Sidebar ─────────────────────────────────────────────── */
function Sidebar({ active, onChange }) {
  return (
    <nav style={{
      width: "var(--sidebar-w)",
      background: "var(--bg-deep)",
      borderRight: "1px solid var(--border-soft)",
      padding: "16px 8px",
      display: "flex", flexDirection: "column",
      gap: 2, flexShrink: 0,
    }}>
      {NAV.map(item => (
        <button key={item.key} onClick={() => onChange(item.key)} style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "8px 12px", borderRadius: "var(--radius-md)",
          border: "none", cursor: "pointer",
          background: active === item.key ? "var(--bg-elevated)" : "transparent",
          color: active === item.key ? "var(--text-primary)" : "var(--text-muted)",
          fontSize: 12, fontWeight: 500,
          transition: "all 0.12s",
          width: "100%", textAlign: "left",
        }}
        onMouseEnter={e => { if (active !== item.key) e.currentTarget.style.background = "var(--bg-base)"; }}
        onMouseLeave={e => { if (active !== item.key) e.currentTarget.style.background = "transparent"; }}
        >
          <span style={{
            fontSize: 14,
            color: active === item.key ? "var(--accent)" : "var(--text-muted)",
          }}>{item.icon}</span>
          {item.label}
        </button>
      ))}
    </nav>
  );
}

/* ── Simple page placeholders ───────────────────────────── */
import TradesPanel    from "./components/panels/TradesPanel.jsx";
import PairsPanel     from "./components/panels/PairsPanel.jsx";
import OpusPanel      from "./components/panels/OpusPanel.jsx";
import EquityChart    from "./components/charts/EquityChart.jsx";
import NewsPanel      from "./components/panels/NewsPanel.jsx";
import { Card }       from "./components/ui/index.jsx";

function TradesPage() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <TradesPanel />
    </div>
  );
}
function PortfolioPage() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <EquityChart />
      <PairsPanel />
      <NewsPanel />
    </div>
  );
}
function OpusPage() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <OpusPanel />
    </div>
  );
}

/* ── Root ────────────────────────────────────────────────── */
export default function App() {
  const [page,       setPage]      = useState("dashboard");
  const [status,     setStatus]    = useState(null);
  const [lastUpdate, setLastUpdate]= useState("—");
  const [pinHash,    setPinHash]   = useState(null);

  // Ambil pin hash dari backend
  useEffect(() => {
    fetch("/api/auth/config")
      .then(r => r.json())
      .then(d => setPinHash(d.pin_hash))
      .catch(() => setPinHash("disabled"));
  }, []);

  useEffect(() => {
    const load = () => {
      api.status()
        .then(d => {
          setStatus(d);
          setLastUpdate(
            new Date().toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
          );
        })
        .catch(() => {});
    };
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);

  const pages = {
    dashboard: <Dashboard />,
    trades:    <TradesPage />,
    portfolio: <PortfolioPage />,
    opus:      <OpusPage />,
  };

  // Tunggu pin hash dimuat
  if (pinHash === null) {
    return (
      <div style={{ minHeight:"100vh", background:"#080c10", display:"flex",
                    alignItems:"center", justifyContent:"center" }}>
        <div style={{ width:8, height:8, borderRadius:"50%",
                      background:"#00d4aa", animation:"pulse-dot 1.2s infinite" }} />
      </div>
    );
  }

  const appContent = (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <Header status={status} lastUpdate={lastUpdate} />
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <Sidebar active={page} onChange={setPage} />
        <main style={{
          flex: 1, overflowY: "auto",
          padding: "20px 24px",
          background: "var(--bg-void)",
        }}>
          {pages[page]}
        </main>
      </div>
    </div>
  );

  // Jika BOT_PIN_HASH tidak diset, langsung tampilkan dashboard
  if (pinHash === "disabled" || !pinHash) return appContent;

  return <PinAuth pinHash={pinHash}>{appContent}</PinAuth>;
}