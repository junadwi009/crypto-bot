/**
 * frontend/src/pages/Dashboard.jsx
 *
 * CMC-style dashboard — SEMUA data dari backend asli.
 * Tidak ada Math.random(), tidak ada simulasi.
 *
 * Data sources:
 *   /api/ticker/all        → ticker tape (5s)
 *   /api/price/{pair}      → price cards (5s)
 *   /api/ohlcv/{pair}      → candlestick chart (30s)
 *   /api/status            → bot status, capital, tier (10s)
 *   /api/trades/open       → open positions (10s)
 *   /api/trades/recent     → trade feed (15s)
 *   /api/events/recent     → signal pipeline + event log (10s)
 *   /api/news/recent       → news feed (30s)
 *   /api/claude/usage      → burn rate, days remaining (60s)
 *   /api/infra/fund        → infra balance (60s)
 *   /api/portfolio/history → equity curve (60s)
 *   /api/portfolio/allocation → capital allocation (60s)
 *   /api/opus/latest-actions  → P0/P1 banner (120s)
 *
 * UPDATED 2026-04-16 — full data-connected rewrite
 */

import { useState, useEffect, useCallback, useRef } from "react";
import {
  AreaChart, Area, ComposedChart, BarChart, Bar,
  Line, XAxis, YAxis, Tooltip, CartesianGrid,
  ResponsiveContainer, ReferenceLine,
} from "recharts";
import { api } from "../api.js";

// ─────────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────────
const fmtUSD  = (n, d = 2) => `$${parseFloat(n || 0).toFixed(d)}`;
const fmtPct  = (n, d = 2) => `${parseFloat(n || 0) >= 0 ? "+" : ""}${parseFloat(n || 0).toFixed(d)}%`;
const fmtPrice = (p) => {
  const v = parseFloat(p || 0);
  return v >= 1000
    ? "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "$" + v.toFixed(v >= 10 ? 3 : 4);
};
const clr = (n) => parseFloat(n || 0) >= 0 ? "#16c784" : "#ea3943";

function hexRgb(hex) {
  const r = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return r ? `${parseInt(r[1], 16)},${parseInt(r[2], 16)},${parseInt(r[3], 16)}` : "255,255,255";
}

// useInterval hook
function useInterval(cb, delay) {
  const ref = useRef(cb);
  useEffect(() => { ref.current = cb; }, [cb]);
  useEffect(() => {
    if (delay === null) return;
    const id = setInterval(() => ref.current(), delay);
    return () => clearInterval(id);
  }, [delay]);
}

// useFetch hook dengan polling
function useFetch(fn, intervalMs, deps = []) {
  const [data, setData]     = useState(null);
  const [error, setError]   = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await fn();
      setData(res);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  // eslint-disable-next-line
  }, deps);

  useEffect(() => { load(); }, [load]);
  useInterval(load, intervalMs);
  return { data, error, loading };
}

// ─────────────────────────────────────────────────────────────────
// OPUS P0 BANNER
// ─────────────────────────────────────────────────────────────────
function OpusBanner() {
  const { data } = useFetch(api.opusLatestActions, 120_000);
  if (!data?.has_critical) return null;
  const p0 = data.p0[0];
  return (
    <div style={{
      background: "rgba(234,57,67,0.1)",
      border: "1px solid rgba(234,57,67,0.3)",
      borderLeft: "3px solid #ea3943",
      padding: "10px 16px",
      display: "flex", alignItems: "center", gap: 12,
      fontFamily: "'DM Mono', monospace",
    }}>
      <span style={{ fontSize: 9, fontWeight: 700, color: "#ea3943",
                     background: "rgba(234,57,67,0.15)", padding: "2px 7px",
                     borderRadius: 4, letterSpacing: "0.08em", flexShrink: 0 }}>
        P0 CRITICAL
      </span>
      <span style={{ fontSize: 12, color: "#f1a0a5" }}>{p0?.title}</span>
      <span style={{ marginLeft: "auto", fontSize: 10, color: "#ea3943", flexShrink: 0 }}>
        Opus Weekly Report
      </span>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// TICKER TAPE — data asli dari /api/ticker/all
// ─────────────────────────────────────────────────────────────────
function TickerTape() {
  const { data } = useFetch(api.tickerAll, 5_000);
  const tickers  = data?.tickers ?? [];

  if (!tickers.length) {
    return (
      <div style={{
        background: "#0b0e11", borderBottom: "1px solid rgba(255,255,255,0.05)",
        height: 34, display: "flex", alignItems: "center",
        padding: "0 16px", gap: 4,
      }}>
        {["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"].map(s => (
          <div key={s} style={{
            padding: "0 16px", borderRight: "1px solid rgba(255,255,255,0.05)",
            fontSize: 11, color: "#3d4553", fontFamily: "'DM Mono', monospace",
          }}>{s} —</div>
        ))}
      </div>
    );
  }

  const items = [...tickers, ...tickers]; // double for seamless loop
  return (
    <div style={{
      background: "#0b0e11",
      borderBottom: "1px solid rgba(255,255,255,0.05)",
      height: 34, overflow: "hidden", position: "relative",
    }}>
      <div style={{
        display: "flex", alignItems: "center", height: "100%",
        animation: "scroll-ticker 35s linear infinite",
        whiteSpace: "nowrap",
      }}>
        {items.map((t, i) => {
          const isUp = t.change_24h_pct >= 0;
          return (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "0 18px", borderRight: "1px solid rgba(255,255,255,0.05)",
              flexShrink: 0,
              background: t.is_active ? "rgba(22,199,132,0.04)" : "transparent",
            }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: t.is_active ? "#16c784" : "#e8edf3",
                              fontFamily: "'DM Mono', monospace" }}>
                {t.symbol.split("/")[0]}
              </span>
              <span style={{ fontSize: 11, color: "#e8edf3", fontFamily: "'DM Mono', monospace" }}>
                {fmtPrice(t.price)}
              </span>
              <span style={{ fontSize: 10, color: isUp ? "#16c784" : "#ea3943",
                              fontFamily: "'DM Mono', monospace" }}>
                {isUp ? "▲" : "▼"} {Math.abs(t.change_24h_pct).toFixed(2)}%
              </span>
            </div>
          );
        })}
      </div>
      <style>{`@keyframes scroll-ticker { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }`}</style>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// PRICE CARD — data asli dari /api/price/{pair}
// ─────────────────────────────────────────────────────────────────
function PriceCard({ pair, isActive }) {
  const slug     = pair.replace("/", "-");
  const { data } = useFetch(() => api.price(slug), 5_000, [slug]);

  const price    = data?.price ?? null;
  const chgPct   = data?.change_24h_pct ?? null;
  const isUp     = chgPct === null ? null : chgPct >= 0;

  const [flash, setFlash] = useState(null);
  const prevPrice = useRef(null);
  useEffect(() => {
    if (price === null) return;
    if (prevPrice.current !== null && price !== prevPrice.current) {
      setFlash(price > prevPrice.current ? "up" : "down");
      const t = setTimeout(() => setFlash(null), 600);
      return () => clearTimeout(t);
    }
    prevPrice.current = price;
  }, [price]);

  return (
    <div style={{
      background: isActive
        ? "linear-gradient(135deg, rgba(22,199,132,0.07) 0%, rgba(11,14,17,0) 60%)"
        : "rgba(255,255,255,0.02)",
      border: isActive ? "1px solid rgba(22,199,132,0.22)" : "1px solid rgba(255,255,255,0.06)",
      borderRadius: 12, padding: "14px 16px", position: "relative",
      transition: "background 0.4s",
    }}>
      {isActive && (
        <div style={{
          position: "absolute", top: 8, right: 8,
          width: 6, height: 6, borderRadius: "50%",
          background: "#16c784", boxShadow: "0 0 8px #16c784",
          animation: "pulse-dot 2s ease-in-out infinite",
        }} />
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
        <div style={{
          width: 26, height: 26, borderRadius: 7,
          background: "rgba(255,255,255,0.07)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 11, fontWeight: 800, color: "#e8edf3",
        }}>
          {pair[0]}
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#e8edf3", lineHeight: 1 }}>
            {pair.split("/")[0]}
          </div>
          <div style={{ fontSize: 9, color: "#5e6673", marginTop: 1 }}>{pair}</div>
        </div>
        {isActive && (
          <div style={{
            marginLeft: "auto", fontSize: 9, fontWeight: 700,
            background: "rgba(22,199,132,0.15)", color: "#16c784",
            padding: "2px 7px", borderRadius: 20, letterSpacing: "0.07em",
            fontFamily: "'DM Mono', monospace",
          }}>TRADING</div>
        )}
      </div>

      <div style={{
        fontSize: 21, fontWeight: 800,
        fontFamily: "'DM Mono', monospace",
        letterSpacing: "-0.03em", lineHeight: 1, marginBottom: 6,
        color: flash === "up" ? "#16c784" : flash === "down" ? "#ea3943" : "#e8edf3",
        transition: "color 0.35s",
      }}>
        {price === null ? "—" : fmtPrice(price)}
      </div>

      {chgPct !== null && (
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          background: isUp ? "rgba(22,199,132,0.12)" : "rgba(234,57,67,0.12)",
          color: isUp ? "#16c784" : "#ea3943",
          padding: "2px 8px", borderRadius: 6,
          fontSize: 10, fontWeight: 600, fontFamily: "'DM Mono', monospace",
        }}>
          {isUp ? "▲" : "▼"} {Math.abs(chgPct).toFixed(2)}% (24h)
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// CAPITAL CARD
// ─────────────────────────────────────────────────────────────────
function CapitalCard({ capital, dailyPnl, tier }) {
  const pnlUp = parseFloat(dailyPnl || 0) >= 0;
  // Tier progression: Seed→300, Growth→500, Pro→1000, Elite→∞
  const TIERS = [
    { name: "SEED",   min: 50,   max: 300  },
    { name: "GROWTH", min: 300,  max: 500  },
    { name: "PRO",    min: 500,  max: 1000 },
    { name: "ELITE",  min: 1000, max: 5000 },
  ];
  const cur = TIERS.find(t => t.name === (tier || "").toUpperCase()) || TIERS[0];
  const progress = Math.min(((capital - cur.min) / (cur.max - cur.min)) * 100, 100);
  const gap      = Math.max(cur.max - capital, 0);

  return (
    <div style={{
      background: "rgba(255,255,255,0.02)",
      border: "1px solid rgba(255,255,255,0.06)",
      borderRadius: 12, padding: "14px 16px",
    }}>
      <div style={{ fontSize: 10, color: "#5e6673", letterSpacing: "0.08em",
                    textTransform: "uppercase", fontFamily: "'DM Mono', monospace", marginBottom: 8 }}>
        Capital
      </div>
      <div style={{
        fontSize: 21, fontWeight: 800, color: "#e8edf3",
        fontFamily: "'DM Mono', monospace", letterSpacing: "-0.02em", lineHeight: 1, marginBottom: 6,
      }}>
        {fmtUSD(capital)}
      </div>
      <div style={{
        display: "inline-flex", gap: 4, alignItems: "center",
        background: pnlUp ? "rgba(22,199,132,0.1)" : "rgba(234,57,67,0.1)",
        color: clr(dailyPnl), padding: "2px 8px", borderRadius: 6,
        fontSize: 10, fontWeight: 600, fontFamily: "'DM Mono', monospace",
        marginBottom: 10,
      }}>
        {pnlUp ? "▲" : "▼"} {pnlUp ? "+" : ""}{fmtUSD(dailyPnl)} today
      </div>
      {/* Tier progress */}
      <div style={{ marginTop: 8 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
          <span style={{ fontSize: 9, color: "#5e6673", fontFamily: "'DM Mono', monospace" }}>
            {cur.name} tier
          </span>
          <span style={{ fontSize: 9, color: "#5e6673", fontFamily: "'DM Mono', monospace" }}>
            {gap > 0 ? `$${gap.toFixed(0)} → ${TIERS[Math.min(TIERS.findIndex(t=>t.name===cur.name)+1, 3)].name}` : "MAX"}
          </span>
        </div>
        <div style={{ height: 3, background: "rgba(255,255,255,0.06)", borderRadius: 2, overflow: "hidden" }}>
          <div style={{
            height: "100%", borderRadius: 2,
            width: `${Math.max(progress, 2)}%`,
            background: "linear-gradient(90deg, #16c784, #00a882)",
            transition: "width 1s ease",
          }} />
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// STAT CARD
// ─────────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, color = "#e8edf3" }) {
  return (
    <div style={{
      background: "#0d1117", border: "1px solid rgba(255,255,255,0.06)",
      borderRadius: 12, padding: "13px 15px",
    }}>
      <div style={{ fontSize: 10, color: "#5e6673", letterSpacing: "0.08em",
                    textTransform: "uppercase", fontFamily: "'DM Mono', monospace", marginBottom: 7 }}>
        {label}
      </div>
      <div style={{
        fontSize: 20, fontWeight: 800, color,
        fontFamily: "'DM Mono', monospace", letterSpacing: "-0.02em", lineHeight: 1, marginBottom: 3,
      }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 10, color: "#3d4553" }}>{sub}</div>}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// CANDLESTICK CHART — data asli dari /api/ohlcv/{pair}
// ─────────────────────────────────────────────────────────────────
const ChartTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  return (
    <div style={{
      background: "#131920", border: "1px solid rgba(255,255,255,0.1)",
      borderRadius: 8, padding: "10px 14px",
      fontFamily: "'DM Mono', monospace", fontSize: 11, color: "#e8edf3",
      minWidth: 170,
    }}>
      <div style={{ color: "#5e6673", marginBottom: 8 }}>{d.time}</div>
      {[["O", d.open, "#e8edf3"], ["H", d.high, "#16c784"], ["L", d.low, "#ea3943"],
        ["C", d.close, d.isUp ? "#16c784" : "#ea3943"]].map(([l, v, c]) => (
        <div key={l} style={{ display: "flex", justifyContent: "space-between", gap: 14, marginBottom: 3 }}>
          <span style={{ color: "#5e6673" }}>{l}</span>
          <span style={{ color: c, fontWeight: 600 }}>{v?.toLocaleString()}</span>
        </div>
      ))}
      {d.rsi !== null && d.rsi !== undefined && (
        <div style={{ borderTop: "1px solid rgba(255,255,255,0.06)", marginTop: 6, paddingTop: 6,
                      display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "#5e6673" }}>RSI</span>
          <span style={{ color: d.rsi > 70 ? "#ea3943" : d.rsi < 30 ? "#16c784" : "#e8edf3", fontWeight: 700 }}>
            {d.rsi}
          </span>
        </div>
      )}
    </div>
  );
};

function ChartPanel({ activePairs = ["BTC/USDT"] }) {
  const [selected, setSelected] = useState(activePairs[0] || "BTC/USDT");
  const [interval, setInterval_] = useState("15");
  const [tab, setTab]            = useState("price");

  const slug      = selected.replace("/", "-");
  const { data, error, loading } = useFetch(
    () => api.ohlcv(slug, interval, 80),
    30_000,
    [slug, interval],
  );

  const candles = data?.candles ?? [];
  const last    = candles[candles.length - 1] ?? {};
  const first   = candles[0] ?? {};
  const chg     = candles.length >= 2
    ? ((last.close - first.close) / first.close * 100).toFixed(2)
    : "0.00";
  const isUp    = parseFloat(chg) >= 0;

  const TFS = ["1", "5", "15", "60", "240", "D"];
  const TF_LABEL = { "1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h", "D": "1D" };

  return (
    <div style={{
      background: "#0d1117", border: "1px solid rgba(255,255,255,0.06)",
      borderRadius: 12, overflow: "hidden",
    }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "10px 14px", borderBottom: "1px solid rgba(255,255,255,0.06)",
        flexWrap: "wrap",
      }}>
        {/* Pair tabs */}
        <div style={{ display: "flex", gap: 4 }}>
          {(activePairs.length ? activePairs : ["BTC/USDT"]).map(p => (
            <button key={p} onClick={() => setSelected(p)} style={{
              padding: "4px 10px", borderRadius: 6, border: "none", cursor: "pointer",
              fontSize: 11, fontWeight: 700, fontFamily: "'DM Mono', monospace",
              background: selected === p ? "rgba(22,199,132,0.15)" : "rgba(255,255,255,0.04)",
              color: selected === p ? "#16c784" : "#5e6673", transition: "all 0.15s",
            }}>{p.split("/")[0]}</button>
          ))}
        </div>

        <div style={{ width: 1, height: 14, background: "rgba(255,255,255,0.07)" }} />

        <span style={{
          fontSize: 16, fontWeight: 800, fontFamily: "'DM Mono', monospace",
          color: isUp ? "#16c784" : "#ea3943",
        }}>
          {last.close ? fmtPrice(last.close) : "—"}
        </span>
        <span style={{ fontSize: 10, color: isUp ? "#16c784" : "#ea3943",
                        fontFamily: "'DM Mono', monospace" }}>
          {isUp ? "▲ +" : "▼ "}{chg}%
        </span>

        {loading && <span style={{ fontSize: 9, color: "#3d4553", fontFamily: "'DM Mono', monospace" }}>loading...</span>}
        {error && <span style={{ fontSize: 9, color: "#ea3943", fontFamily: "'DM Mono', monospace" }}>offline</span>}

        {/* TF buttons */}
        <div style={{ marginLeft: "auto", display: "flex", gap: 2 }}>
          {TFS.map(t => (
            <button key={t} onClick={() => setInterval_(t)} style={{
              padding: "3px 8px", borderRadius: 4, border: "none", cursor: "pointer",
              fontSize: 10, fontWeight: 600, fontFamily: "'DM Mono', monospace",
              background: interval === t ? "rgba(255,255,255,0.1)" : "transparent",
              color: interval === t ? "#e8edf3" : "#5e6673",
            }}>{TF_LABEL[t]}</button>
          ))}
        </div>

        {/* Tab price/rsi */}
        <div style={{ display: "flex", background: "rgba(255,255,255,0.04)", borderRadius: 6, padding: 2 }}>
          {["price", "rsi"].map(t => (
            <button key={t} onClick={() => setTab(t)} style={{
              padding: "3px 10px", borderRadius: 5, border: "none", cursor: "pointer",
              fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em",
              background: tab === t ? "rgba(255,255,255,0.1)" : "transparent",
              color: tab === t ? "#e8edf3" : "#5e6673",
            }}>{t}</button>
          ))}
        </div>
      </div>

      {/* Chart body */}
      {tab === "price" ? (
        <div>
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={candles} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="2 4" stroke="rgba(255,255,255,0.03)" vertical={false} />
              <XAxis dataKey="time" tick={{ fill: "#3d4553", fontSize: 9, fontFamily: "'DM Mono',monospace" }}
                     axisLine={false} tickLine={false} interval={Math.floor(candles.length / 6)} />
              <YAxis orientation="right" tick={{ fill: "#3d4553", fontSize: 9, fontFamily: "'DM Mono',monospace" }}
                     axisLine={false} tickLine={false} width={62}
                     tickFormatter={v => v >= 1000 ? `$${(v / 1000).toFixed(1)}k` : `$${v}`}
                     domain={["auto", "auto"]} />
              <Tooltip content={<ChartTooltip />} />
              <Line dataKey="bbUpper" stroke="rgba(99,102,241,0.3)" dot={false} strokeWidth={1}
                    strokeDasharray="3 3" connectNulls />
              <Line dataKey="bbLower" stroke="rgba(99,102,241,0.3)" dot={false} strokeWidth={1}
                    strokeDasharray="3 3" connectNulls />
              <Line dataKey="bbMid" stroke="rgba(99,102,241,0.18)" dot={false} strokeWidth={1} connectNulls />
              <Bar dataKey="close" maxBarSize={7}
                   fill="#16c784"
                   radius={[1, 1, 0, 0]} />
            </ComposedChart>
          </ResponsiveContainer>
          {/* Volume */}
          <ResponsiveContainer width="100%" height={50}>
            <BarChart data={candles} margin={{ top: 0, right: 4, bottom: 0, left: 0 }}>
              <XAxis dataKey="time" hide />
              <YAxis hide />
              <Bar dataKey="volume" maxBarSize={7} radius={[1, 1, 0, 0]}
                   fill="#3d4553" opacity={0.6} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={290}>
          <ComposedChart data={candles} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="2 4" stroke="rgba(255,255,255,0.03)" vertical={false} />
            <XAxis dataKey="time" tick={{ fill: "#3d4553", fontSize: 9 }} axisLine={false} tickLine={false}
                   interval={Math.floor(candles.length / 6)} />
            <YAxis orientation="right" domain={[0, 100]}
                   tick={{ fill: "#3d4553", fontSize: 9 }} axisLine={false} tickLine={false} width={35} />
            <Tooltip content={<ChartTooltip />} />
            <ReferenceLine y={70} stroke="rgba(234,57,67,0.4)" strokeDasharray="3 3" />
            <ReferenceLine y={30} stroke="rgba(22,199,132,0.4)" strokeDasharray="3 3" />
            <Area dataKey="rsi" stroke="#a78bfa" strokeWidth={1.5}
                  fill="rgba(167,139,250,0.08)" dot={false} connectNulls />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// SIGNAL PIPELINE — dari /api/events/recent (event_type: trade_opened, order_error, dll)
// ─────────────────────────────────────────────────────────────────
const PIPELINE_STAGES = [
  { key: "rule_based", label: "Rule",   icon: "⚙", color: "#4fc3f7" },
  { key: "haiku",      label: "Haiku",  icon: "⚡", color: "#a78bfa" },
  { key: "sonnet",     label: "Sonnet", icon: "◆",  color: "#f59e0b" },
  { key: "guard",      label: "Guard",  icon: "◉",  color: "#fb7185" },
  { key: "exec",       label: "Exec",   icon: "▶",  color: "#16c784" },
];

function SignalPipeline({ activePairs = [] }) {
  const { data } = useFetch(() => api.recentEvents(2), 10_000);
  const events   = data?.events ?? [];

  // Ambil trade events terakhir
  const tradeEvents = events.filter(e =>
    ["trade_opened", "order_error", "signal_rejected"].includes(e.event_type)
  );
  const lastTrade = tradeEvents[0] ?? null;

  // Derive stage states dari event terbaru
  // Jika ada trade_opened → semua stage OK
  // Jika ada order_error  → exec gagal
  // Jika tidak ada        → semua idle
  const deriveStages = () => {
    if (!lastTrade) return {};
    const src = lastTrade.data?.source ?? "rule_based";
    const isError = lastTrade.event_type === "order_error";
    const stages = {};

    // Stage sampai source = aktif OK
    const srcIdx = PIPELINE_STAGES.findIndex(s => s.key === src);
    PIPELINE_STAGES.forEach((s, i) => {
      if (i <= Math.max(srcIdx, 0)) stages[s.key] = { active: true, ok: !isError || i < PIPELINE_STAGES.length - 1 };
      if (i === PIPELINE_STAGES.length - 1) stages[s.key] = { active: true, ok: !isError };
    });
    return stages;
  };

  const stages = deriveStages();

  return (
    <div style={{
      background: "#0d1117", border: "1px solid rgba(255,255,255,0.06)",
      borderRadius: 12, padding: "14px 16px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", color: "#5e6673",
                        textTransform: "uppercase", fontFamily: "'DM Mono', monospace" }}>
          Signal Pipeline
        </span>
        <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#16c784",
                       boxShadow: "0 0 5px #16c784", animation: "pulse-dot 2s ease-in-out infinite",
                       marginTop: 2 }} />
      </div>

      <div style={{ display: "flex", alignItems: "center" }}>
        {PIPELINE_STAGES.map((stage, i) => {
          const s    = stages[stage.key];
          const on   = s?.active;
          const ok   = s?.ok;
          const col  = on ? (ok ? stage.color : "#ea3943") : "#3d4553";
          const bg   = on ? (ok ? `rgba(${hexRgb(stage.color)},0.12)` : "rgba(234,57,67,0.1)") : "rgba(255,255,255,0.03)";
          const bdr  = on ? (ok ? `${stage.color}40` : "rgba(234,57,67,0.3)") : "rgba(255,255,255,0.05)";

          return (
            <div key={stage.key} style={{ display: "flex", alignItems: "center", flex: 1 }}>
              <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 5 }}>
                <div style={{
                  width: 34, height: 34, borderRadius: 9,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 14, background: bg, border: `1px solid ${bdr}`,
                  boxShadow: on && ok ? `0 0 10px ${stage.color}25` : "none",
                  transition: "all 0.4s",
                }}>{stage.icon}</div>
                <span style={{
                  fontSize: 9, fontFamily: "'DM Mono', monospace", color: col,
                  textAlign: "center", letterSpacing: "0.03em", transition: "color 0.4s",
                }}>{stage.label}</span>
              </div>
              {i < PIPELINE_STAGES.length - 1 && (
                <div style={{
                  width: 16, height: 1, flexShrink: 0,
                  background: on && ok
                    ? `linear-gradient(90deg,${stage.color},${PIPELINE_STAGES[i + 1].color})`
                    : "rgba(255,255,255,0.06)",
                  transition: "background 0.5s",
                }} />
              )}
            </div>
          );
        })}
      </div>

      {lastTrade && (
        <div style={{
          marginTop: 12, display: "flex", alignItems: "center", gap: 8,
          padding: "7px 10px", borderRadius: 7,
          background: lastTrade.event_type === "order_error" ? "rgba(234,57,67,0.08)" :
                      lastTrade.data?.side === "buy" ? "rgba(22,199,132,0.08)" : "rgba(234,57,67,0.08)",
          border: `1px solid ${lastTrade.event_type === "order_error" ? "rgba(234,57,67,0.2)" :
                   lastTrade.data?.side === "buy" ? "rgba(22,199,132,0.2)" : "rgba(234,57,67,0.2)"}`,
          fontFamily: "'DM Mono', monospace",
        }}>
          <span style={{ fontSize: 9, color: "#5e6673" }}>LAST</span>
          {lastTrade.event_type === "order_error" ? (
            <span style={{ fontSize: 11, color: "#ea3943" }}>ERROR</span>
          ) : (
            <span style={{ fontSize: 11, fontWeight: 700,
                            color: lastTrade.data?.side === "buy" ? "#16c784" : "#ea3943" }}>
              {(lastTrade.data?.side || "").toUpperCase()}
            </span>
          )}
          <span style={{ fontSize: 11, color: "#e8edf3" }}>{lastTrade.data?.pair ?? ""}</span>
          {lastTrade.data?.confidence && (
            <span style={{ fontSize: 9, color: "#5e6673" }}>
              conf={parseFloat(lastTrade.data.confidence).toFixed(2)}
            </span>
          )}
          <span style={{ marginLeft: "auto", fontSize: 9, color: "#5e6673" }}>
            {new Date(lastTrade.created_at).toLocaleTimeString("id-ID")}
          </span>
        </div>
      )}

      {!lastTrade && (
        <div style={{ marginTop: 10, fontSize: 10, color: "#3d4553",
                       fontFamily: "'DM Mono', monospace", textAlign: "center" }}>
          Belum ada sinyal dalam 2 jam terakhir
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// EQUITY MINI CHART — dari /api/portfolio/history
// ─────────────────────────────────────────────────────────────────
function EquityMini() {
  const { data } = useFetch(() => api.portfolioHistory(14), 60_000);
  const raw      = data?.data ?? [];
  const history  = raw.slice(0, 14).reverse().map(d => ({
    date:    (d.snapshot_date ?? "").slice(5),
    capital: parseFloat(d.total_capital ?? 0),
    pnl:     parseFloat(d.daily_pnl ?? 0),
  }));

  const isUp = history.length >= 2 && history[history.length - 1].capital >= history[0].capital;

  return (
    <div style={{
      background: "#0d1117", border: "1px solid rgba(255,255,255,0.06)",
      borderRadius: 12, padding: "14px 16px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", color: "#5e6673",
                        textTransform: "uppercase", fontFamily: "'DM Mono', monospace" }}>
          Equity Curve
        </span>
        <span style={{ fontSize: 9, color: "#3d4553", fontFamily: "'DM Mono', monospace" }}>14 hari</span>
      </div>
      {history.length < 2 ? (
        <div style={{ height: 80, display: "flex", alignItems: "center", justifyContent: "center",
                       fontSize: 10, color: "#3d4553", fontFamily: "'DM Mono', monospace" }}>
          Data belum tersedia
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={80}>
          <AreaChart data={history} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={isUp ? "#16c784" : "#ea3943"} stopOpacity={0.2} />
                <stop offset="95%" stopColor={isUp ? "#16c784" : "#ea3943"} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area dataKey="capital" stroke={isUp ? "#16c784" : "#ea3943"} strokeWidth={1.5}
                  fill="url(#eqGrad)" dot={false} />
            <Tooltip
              formatter={v => [fmtUSD(v), "Capital"]}
              contentStyle={{ background: "#131920", border: "1px solid rgba(255,255,255,0.1)",
                              borderRadius: 6, fontSize: 10, fontFamily: "'DM Mono', monospace" }}
              labelStyle={{ color: "#5e6673" }}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// TRADE FEED — dari /api/trades/open + /api/trades/recent (data asli)
// ─────────────────────────────────────────────────────────────────
function TradeFeed() {
  const { data: openData }   = useFetch(api.openTrades, 10_000);
  const { data: recentData } = useFetch(() => api.recentTrades(7), 15_000);

  const openTrades   = openData?.trades ?? [];
  const recentTrades = recentData?.trades ?? [];

  const SRC_ICON = { haiku: "⚡H", sonnet: "◆S", rule_based: "⚙R", news: "📰N" };

  const allTrades = [
    ...openTrades.map(t => ({ ...t, _isOpen: true })),
    ...recentTrades.slice(0, 6),
  ];

  return (
    <div style={{
      background: "#0d1117", border: "1px solid rgba(255,255,255,0.06)",
      borderRadius: 12, overflow: "hidden",
    }}>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "10px 14px", borderBottom: "1px solid rgba(255,255,255,0.06)",
      }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", color: "#5e6673",
                        textTransform: "uppercase", fontFamily: "'DM Mono', monospace" }}>
          Trades
        </span>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {openTrades.length > 0 && (
            <span style={{ fontSize: 9, background: "rgba(22,199,132,0.15)", color: "#16c784",
                            padding: "2px 6px", borderRadius: 10, fontFamily: "'DM Mono', monospace" }}>
              {openTrades.length} open
            </span>
          )}
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#16c784",
                         boxShadow: "0 0 5px #16c784", animation: "pulse-dot 2s ease-in-out infinite" }} />
        </div>
      </div>
      {allTrades.length === 0 ? (
        <div style={{ padding: "24px 14px", textAlign: "center",
                       color: "#3d4553", fontSize: 11, fontFamily: "'DM Mono', monospace" }}>
          Belum ada trade
        </div>
      ) : (
        allTrades.slice(0, 8).map((t, i) => {
          const isBuy = t.side === "buy";
          const pnl   = parseFloat(t.pnl_usd || 0);
          const isOpen = t._isOpen || t.status === "open";
          return (
            <div key={t.id || i} style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "8px 14px", borderBottom: "1px solid rgba(255,255,255,0.03)",
              background: isOpen ? "rgba(22,199,132,0.03)" : "transparent",
            }}>
              <div style={{
                width: 28, height: 17, borderRadius: 4, flexShrink: 0,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 8, fontWeight: 700, letterSpacing: "0.04em",
                fontFamily: "'DM Mono', monospace",
                background: isBuy ? "rgba(22,199,132,0.15)" : "rgba(234,57,67,0.15)",
                color: isBuy ? "#16c784" : "#ea3943",
              }}>
                {t.side?.toUpperCase()}
              </div>
              <span style={{ fontSize: 11, fontWeight: 600, fontFamily: "'DM Mono', monospace",
                              color: "#e8edf3" }}>{t.pair}</span>
              <span style={{ fontSize: 10, color: "#5e6673" }}>{fmtUSD(t.amount_usd)}</span>
              {isOpen ? (
                <span style={{ marginLeft: "auto", fontSize: 9, color: "#16c784",
                                fontFamily: "'DM Mono', monospace" }}>OPEN</span>
              ) : (
                <span style={{
                  marginLeft: "auto", fontSize: 11, fontWeight: 700,
                  fontFamily: "'DM Mono', monospace",
                  color: pnl >= 0 ? "#16c784" : "#ea3943",
                }}>
                  {pnl >= 0 ? "+" : ""}{fmtUSD(pnl)}
                </span>
              )}
              <span style={{ fontSize: 9, color: "#3d4553", flexShrink: 0 }}>
                {SRC_ICON[t.trigger_source] ?? "⚙R"}
              </span>
            </div>
          );
        })
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// NEWS FEED — dari /api/news/recent
// ─────────────────────────────────────────────────────────────────
function NewsFeed() {
  const { data } = useFetch(() => api.recentNews(24), 30_000);
  const news     = (data?.news ?? []).slice(0, 8);

  const sentColor = (s) => {
    const v = parseFloat(s || 0);
    return v > 0.2 ? "#16c784" : v < -0.2 ? "#ea3943" : "#5e6673";
  };
  const actionColor = (a) =>
    a === "opportunity" ? "#16c784" : a === "close" || a === "reduce_risk" ? "#ea3943" :
    a === "hold" ? "#5e6673" : "#e8edf3";

  return (
    <div style={{
      background: "#0d1117", border: "1px solid rgba(255,255,255,0.06)",
      borderRadius: 12, overflow: "hidden",
    }}>
      <div style={{
        padding: "10px 14px", borderBottom: "1px solid rgba(255,255,255,0.06)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", color: "#5e6673",
                        textTransform: "uppercase", fontFamily: "'DM Mono', monospace" }}>
          News Pipeline
        </span>
        <span style={{ fontSize: 9, color: "#3d4553", fontFamily: "'DM Mono', monospace" }}>24h</span>
      </div>
      {news.length === 0 ? (
        <div style={{ padding: "20px 14px", textAlign: "center",
                       color: "#3d4553", fontSize: 11, fontFamily: "'DM Mono', monospace" }}>
          Belum ada berita diproses
        </div>
      ) : (
        news.map((n, i) => {
          const sent  = parseFloat(n.haiku_sentiment || 0);
          const rel   = parseFloat(n.haiku_relevance || 0);
          return (
            <div key={i} style={{
              padding: "9px 14px", borderBottom: "1px solid rgba(255,255,255,0.03)",
            }}>
              <div style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 4 }}>
                <div style={{
                  width: 4, height: 4, borderRadius: "50%", flexShrink: 0, marginTop: 5,
                  background: sentColor(sent),
                }} />
                <span style={{ fontSize: 11, color: "#c0c8d4", lineHeight: 1.45, flex: 1 }}>
                  {n.headline}
                </span>
              </div>
              <div style={{ display: "flex", gap: 8, marginLeft: 12, flexWrap: "wrap" }}>
                <span style={{ fontSize: 9, color: "#3d4553", fontFamily: "'DM Mono', monospace" }}>
                  {n.source}
                </span>
                <span style={{ fontSize: 9, color: sentColor(sent), fontFamily: "'DM Mono', monospace" }}>
                  sent={sent > 0 ? "+" : ""}{sent.toFixed(2)}
                </span>
                <span style={{ fontSize: 9, color: "#3d4553", fontFamily: "'DM Mono', monospace" }}>
                  rel={rel.toFixed(2)}
                </span>
                {n.sonnet_action && (
                  <span style={{ fontSize: 9, color: actionColor(n.sonnet_action),
                                  fontFamily: "'DM Mono', monospace", fontWeight: 700 }}>
                    → {n.sonnet_action}
                  </span>
                )}
                {(n.pairs_mentioned ?? []).map(p => (
                  <span key={p} style={{ fontSize: 9, color: "#16c784",
                                          fontFamily: "'DM Mono', monospace" }}>
                    {p.split("/")[0]}
                  </span>
                ))}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// CLAUDE USAGE CARD — burn rate + days remaining
// ─────────────────────────────────────────────────────────────────
function ClaudeCard() {
  const { data } = useFetch(api.claudeUsage, 60_000);

  const cost     = parseFloat(data?.monthly_cost_usd || 0);
  const balance  = parseFloat(data?.estimated_balance || 0);
  const burn     = parseFloat(data?.burn_rate_per_day || 0);
  const daysLeft = parseFloat(data?.days_remaining || 0);
  const limit    = parseFloat(data?.spending_limit || 30);
  const mode     = data?.mode ?? "normal";

  const spendPct = Math.min((cost / limit) * 100, 100);
  const modeColor = mode === "economy" ? "#f59e0b" : mode === "critical" ? "#ea3943" : "#16c784";

  return (
    <div style={{
      background: "#0d1117", border: "1px solid rgba(255,255,255,0.06)",
      borderRadius: 12, padding: "14px 16px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", color: "#5e6673",
                        textTransform: "uppercase", fontFamily: "'DM Mono', monospace" }}>
          Claude Budget
        </span>
        <span style={{
          fontSize: 9, fontWeight: 700, letterSpacing: "0.07em",
          background: `rgba(${hexRgb(modeColor)},0.15)`, color: modeColor,
          padding: "2px 7px", borderRadius: 20, fontFamily: "'DM Mono', monospace",
        }}>
          {mode.toUpperCase()}
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
        {[
          { l: "Spent", v: fmtUSD(cost, 3), c: cost > limit * 0.8 ? "#ea3943" : "#e8edf3" },
          { l: "Balance", v: fmtUSD(balance, 2), c: balance < 5 ? "#ea3943" : "#16c784" },
          { l: "Burn/day", v: fmtUSD(burn, 3), c: "#a78bfa" },
          { l: "Days left", v: daysLeft > 0 ? `~${daysLeft.toFixed(0)}d` : "—",
            c: daysLeft < 7 ? "#ea3943" : daysLeft < 14 ? "#f59e0b" : "#16c784" },
        ].map(({ l, v, c }) => (
          <div key={l}>
            <div style={{ fontSize: 9, color: "#3d4553", fontFamily: "'DM Mono', monospace",
                           marginBottom: 2 }}>{l}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: c,
                           fontFamily: "'DM Mono', monospace" }}>{v}</div>
          </div>
        ))}
      </div>

      {/* Spend bar */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
          <span style={{ fontSize: 9, color: "#3d4553", fontFamily: "'DM Mono', monospace" }}>
            {fmtUSD(cost, 2)} / ${limit} limit
          </span>
          <span style={{ fontSize: 9, color: spendPct > 80 ? "#ea3943" : "#3d4553",
                          fontFamily: "'DM Mono', monospace" }}>
            {spendPct.toFixed(0)}%
          </span>
        </div>
        <div style={{ height: 3, background: "rgba(255,255,255,0.06)", borderRadius: 2 }}>
          <div style={{
            height: "100%", borderRadius: 2,
            width: `${Math.max(spendPct, 1)}%`,
            background: spendPct > 80 ? "#ea3943" : spendPct > 60 ? "#f59e0b" : "#a78bfa",
            transition: "width 1s ease",
          }} />
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// INFRA FUND CARD
// ─────────────────────────────────────────────────────────────────
function InfraCard() {
  const { data } = useFetch(api.infraFund, 60_000);
  const balance  = parseFloat(data?.current_balance ?? 0);
  const txns     = data?.transactions ?? [];
  const lastTxn  = txns[0];

  // Render cost Render ~$7/mo
  const renderCost = 7;
  const coverMonths = (balance / renderCost).toFixed(1);

  return (
    <div style={{
      background: "#0d1117", border: "1px solid rgba(255,255,255,0.06)",
      borderRadius: 12, padding: "14px 16px",
    }}>
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", color: "#5e6673",
                     textTransform: "uppercase", fontFamily: "'DM Mono', monospace", marginBottom: 10 }}>
        Infra Fund
      </div>
      <div style={{
        fontSize: 20, fontWeight: 800, color: balance > 7 ? "#16c784" : "#ea3943",
        fontFamily: "'DM Mono', monospace", letterSpacing: "-0.02em", marginBottom: 4,
      }}>
        {fmtUSD(balance)}
      </div>
      <div style={{ fontSize: 10, color: "#5e6673", marginBottom: 10 }}>
        Cover ~{coverMonths} bulan Render ($7/mo)
      </div>
      {lastTxn && (
        <div style={{ fontSize: 10, color: "#3d4553", fontFamily: "'DM Mono', monospace" }}>
          Last: {lastTxn.type === "credit" ? "+" : "-"}{fmtUSD(lastTxn.amount)} — {lastTxn.description?.slice(0, 30)}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// EVENT LOG — dari /api/events/recent
// ─────────────────────────────────────────────────────────────────
function EventLog() {
  const { data } = useFetch(() => api.recentEvents(24), 10_000);
  const events   = (data?.events ?? []).slice(0, 10);

  const evtColor = (e) => {
    if (e.severity === "critical") return "#ea3943";
    if (e.severity === "warning")  return "#f59e0b";
    const t = e.event_type ?? "";
    if (t === "trade_opened")            return "#16c784";
    if (t === "trade_closed")            return "#4fc3f7";
    if (t === "circuit_breaker_tripped") return "#ea3943";
    if (t === "tier_upgraded")           return "#a78bfa";
    return "#5e6673";
  };

  return (
    <div style={{
      background: "#0d1117", border: "1px solid rgba(255,255,255,0.06)",
      borderRadius: 12, overflow: "hidden",
    }}>
      <div style={{ padding: "10px 14px", borderBottom: "1px solid rgba(255,255,255,0.06)",
                     display: "flex", justifyContent: "space-between" }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", color: "#5e6673",
                        textTransform: "uppercase", fontFamily: "'DM Mono', monospace" }}>
          Event Log
        </span>
        <span style={{ fontSize: 9, color: "#3d4553", fontFamily: "'DM Mono', monospace" }}>24h</span>
      </div>
      {events.length === 0 ? (
        <div style={{ padding: "20px 14px", textAlign: "center",
                       color: "#3d4553", fontSize: 11, fontFamily: "'DM Mono', monospace" }}>
          Tidak ada event
        </div>
      ) : (
        events.map((e, i) => (
          <div key={e.id || i} style={{
            display: "flex", alignItems: "flex-start", gap: 8,
            padding: "7px 14px", borderBottom: "1px solid rgba(255,255,255,0.03)",
          }}>
            <div style={{
              width: 5, height: 5, borderRadius: "50%",
              background: evtColor(e), flexShrink: 0, marginTop: 4,
            }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 11, color: "#c0c8d4", lineHeight: 1.4,
                             overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {e.message}
              </div>
              <div style={{ fontSize: 9, color: "#3d4553", fontFamily: "'DM Mono', monospace", marginTop: 2 }}>
                {e.event_type} · {new Date(e.created_at).toLocaleTimeString("id-ID")}
              </div>
            </div>
          </div>
        ))
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// MAIN DASHBOARD
// ─────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const { data: status }  = useFetch(api.status, 10_000);
  const { data: summary } = useFetch(api.portfolioSummary, 30_000);

  const [now, setNow] = useState(new Date());
  useInterval(() => setNow(new Date()), 1000);

  const capital     = parseFloat(status?.capital ?? 0);
  const dailyPnl    = parseFloat(status?.daily_pnl ?? 0);
  const winRate     = parseFloat(summary?.win_rate ?? 0);
  const drawdown    = parseFloat(summary?.max_drawdown ?? 0);
  const activePairs = status?.active_pairs ?? [];
  const tier        = status?.tier ?? "seed";
  const mode        = status?.paper_trade ? "PAPER" : "LIVE";
  const botStatus   = status?.status ?? "running";
  const cbTripped   = status?.circuit_breaker?.tripped;
  const openCount   = status?.open_trades ?? 0;

  return (
    <div style={{ fontFamily: "'DM Sans', 'Space Grotesk', sans-serif", minHeight: "100vh" }}>

      {/* ── Global styles ── */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=DM+Sans:wght@400;500;600;700;800&display=swap');
        @keyframes pulse-dot { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.4;transform:scale(0.8)} }
        @keyframes scroll-ticker { 0%{transform:translateX(0)} 100%{transform:translateX(-50%)} }
      `}</style>

      {/* ── Ticker tape ── */}
      <TickerTape />

      {/* ── Opus P0 banner ── */}
      <OpusBanner />

      {/* ── Circuit breaker banner ── */}
      {cbTripped && (
        <div style={{
          background: "rgba(234,57,67,0.15)", borderBottom: "1px solid rgba(234,57,67,0.3)",
          padding: "8px 16px", display: "flex", alignItems: "center", gap: 10,
          fontFamily: "'DM Mono', monospace",
        }}>
          <span style={{ fontSize: 9, fontWeight: 700, color: "#ea3943",
                          background: "rgba(234,57,67,0.2)", padding: "2px 7px", borderRadius: 4 }}>
            ⚠ CIRCUIT BREAKER
          </span>
          <span style={{ fontSize: 12, color: "#f1a0a5" }}>
            Bot dihentikan sementara — drawdown melebihi batas. Trading tidak aktif.
          </span>
        </div>
      )}

      {/* ── Top bar ── */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "8px 20px", background: "#080b0f",
        borderBottom: "1px solid rgba(255,255,255,0.04)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{
              width: 7, height: 7, borderRadius: "50%",
              background: botStatus === "running" ? "#16c784" : "#ea3943",
              boxShadow: `0 0 8px ${botStatus === "running" ? "#16c784" : "#ea3943"}`,
              animation: "pulse-dot 2s ease-in-out infinite",
            }} />
            <span style={{ fontSize: 10, color: "#5e6673", fontFamily: "'DM Mono', monospace",
                            letterSpacing: "0.08em" }}>
              {botStatus.toUpperCase()}
            </span>
          </div>
          {[
            { label: mode, color: mode === "PAPER" ? "#f59e0b" : "#16c784" },
            { label: tier.toUpperCase(), color: "#4fc3f7" },
          ].map(({ label, color }) => (
            <div key={label} style={{
              fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase",
              fontFamily: "'DM Mono', monospace",
              background: `rgba(${hexRgb(color)},0.15)`,
              color, padding: "2px 8px", borderRadius: 20,
              border: `1px solid rgba(${hexRgb(color)},0.25)`,
            }}>{label}</div>
          ))}
          {activePairs.map(p => (
            <div key={p} style={{
              fontSize: 9, fontWeight: 700, color: "#16c784",
              background: "rgba(22,199,132,0.1)", padding: "2px 8px", borderRadius: 20,
              border: "1px solid rgba(22,199,132,0.2)", fontFamily: "'DM Mono', monospace",
            }}>{p}</div>
          ))}
        </div>
        <span style={{ fontSize: 11, color: "#3d4553", fontFamily: "'DM Mono', monospace" }}>
          {now.toLocaleTimeString("id-ID")} WIB
        </span>
      </div>

      {/* ── Content ── */}
      <div style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 14 }}>

        {/* Row 1: Price cards */}
        <div style={{
          display: "grid",
          gridTemplateColumns: `repeat(${Math.min(activePairs.length + 1, 4)}, 1fr)`,
          gap: 10,
        }}>
          {activePairs.map(p => <PriceCard key={p} pair={p} isActive />)}
          <CapitalCard capital={capital} dailyPnl={dailyPnl} tier={tier} />
        </div>

        {/* Row 2: Stats */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
          <StatCard
            label="Win Rate"
            value={winRate > 0 ? `${(winRate * 100).toFixed(1)}%` : "—"}
            color={winRate >= 0.6 ? "#16c784" : winRate >= 0.5 ? "#f59e0b" : winRate > 0 ? "#ea3943" : "#5e6673"}
            sub={winRate === 0 ? "Belum ada trade" : undefined}
          />
          <StatCard
            label="Max Drawdown"
            value={`${(drawdown * 100).toFixed(1)}%`}
            color={drawdown > 0.10 ? "#ea3943" : drawdown > 0.05 ? "#f59e0b" : "#16c784"}
          />
          <StatCard
            label="Open Positions"
            value={openCount}
            color="#4fc3f7"
            sub={`dari ${activePairs.length} pair aktif`}
          />
          <StatCard
            label="7d Trades"
            value={summary?.total_trades ?? "—"}
            color="#a78bfa"
            sub={summary?.total_pnl !== undefined ? `PnL: ${fmtUSD(summary.total_pnl)}` : undefined}
          />
        </div>

        {/* Row 3: Chart + right column */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 14 }}>
          <ChartPanel activePairs={activePairs.length ? activePairs : ["BTC/USDT"]} />
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <SignalPipeline activePairs={activePairs} />
            <EquityMini />
          </div>
        </div>

        {/* Row 4: Trade feed + News */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <TradeFeed />
          <NewsFeed />
        </div>

        {/* Row 5: Claude + Infra + Event log */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
          <ClaudeCard />
          <InfraCard />
          <EventLog />
        </div>

      </div>
    </div>
  );
}