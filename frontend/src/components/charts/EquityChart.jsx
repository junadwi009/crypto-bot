import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, Tooltip, CartesianGrid,
  ResponsiveContainer, ReferenceLine, ComposedChart, Line,
} from "recharts";
import { useState } from "react";
import { Card, SectionTitle, Badge } from "../ui/index.jsx";
import { useHistory } from "../../hooks/useData.js";

const axisTick = { fontSize: 9, fill: "#4a5568", fontFamily: "var(--font-mono)" };

export default function EquityChart() {
  const [days, setDays] = useState(14);
  const { data: raw }   = useHistory(days);

  const history = (raw?.data ?? []).slice(0, days).reverse().map(d => ({
    date:     d.snapshot_date?.slice(5) || "",
    capital:  parseFloat(d.total_capital || 0),
    pnl:      parseFloat(d.daily_pnl || 0),
    drawdown: parseFloat(d.drawdown_pct || 0) * 100,
  }));

  const first  = history[0]?.capital || 213;
  const last   = history[history.length - 1]?.capital || 213;
  const totalReturn = ((last - first) / first * 100).toFixed(2);
  const isPos  = parseFloat(totalReturn) >= 0;

  const ttStyle = {
    background: "var(--bg-elevated)", border: "1px solid var(--border-mid)",
    borderRadius: 8, fontSize: 11, fontFamily: "var(--font-mono)",
    color: "var(--text-primary)",
  };

  return (
    <Card style={{ display: "flex", flexDirection: "column" }}>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "12px 16px", borderBottom: "1px solid var(--border-dim)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.12em",
                          textTransform: "uppercase", color: "var(--text-muted)",
                          fontFamily: "var(--font-mono)" }}>EQUITY CURVE</span>
          <Badge color={isPos ? "green" : "red"}>{isPos ? "+" : ""}{totalReturn}%</Badge>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {[7, 14, 30].map(d => (
            <button key={d} onClick={() => setDays(d)} style={{
              padding: "2px 8px", borderRadius: 4, border: "none",
              background: days === d ? "var(--accent-dim)" : "transparent",
              color: days === d ? "var(--accent)" : "var(--text-muted)",
              fontSize: 10, fontWeight: 600, cursor: "pointer",
              fontFamily: "var(--font-mono)",
            }}>{d}D</button>
          ))}
        </div>
      </div>

      {/* Capital area */}
      <div style={{ padding: "12px 4px 0" }}>
        <div style={{ padding: "0 12px 6px", fontSize: 10, color: "var(--text-muted)",
                      fontFamily: "var(--font-mono)" }}>CAPITAL (USD)</div>
        <ResponsiveContainer width="100%" height={140}>
          <AreaChart data={history} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="capGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%"   stopColor="#00d4aa" stopOpacity={0.3} />
                <stop offset="100%" stopColor="#00d4aa" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />
            <XAxis dataKey="date" tick={axisTick} axisLine={false} tickLine={false}
                   interval={Math.floor(history.length / 5)} />
            <YAxis tick={axisTick} axisLine={false} tickLine={false} width={56}
                   tickFormatter={v => "$" + v.toFixed(0)} domain={["auto","auto"]} />
            <Tooltip contentStyle={ttStyle}
                     formatter={v => ["$" + v.toFixed(2), "Capital"]} />
            <ReferenceLine y={first} stroke="rgba(255,255,255,0.1)" strokeDasharray="4 2" />
            <Area type="monotone" dataKey="capital" stroke="#00d4aa" strokeWidth={2}
                  fill="url(#capGrad)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Daily PnL bars */}
      <div style={{ padding: "12px 4px 0" }}>
        <div style={{ padding: "0 12px 6px", fontSize: 10, color: "var(--text-muted)",
                      fontFamily: "var(--font-mono)" }}>DAILY PnL</div>
        <ResponsiveContainer width="100%" height={80}>
          <BarChart data={history} margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />
            <XAxis dataKey="date" tick={axisTick} axisLine={false} tickLine={false}
                   interval={Math.floor(history.length / 5)} />
            <YAxis tick={axisTick} axisLine={false} tickLine={false} width={40}
                   tickFormatter={v => "$" + v.toFixed(0)} />
            <Tooltip contentStyle={ttStyle}
                     formatter={v => [(v >= 0 ? "+" : "") + "$" + v.toFixed(2), "PnL"]} />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.12)" />
            <Bar dataKey="pnl" radius={[2,2,0,0]} isAnimationActive={false}
                 fill="#00d4aa">
              {history.map((entry, i) => (
                <rect key={i} fill={entry.pnl >= 0 ? "rgba(0,212,170,0.7)" : "rgba(255,71,87,0.7)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Drawdown */}
      <div style={{ padding: "12px 4px 8px" }}>
        <div style={{ padding: "0 12px 6px", fontSize: 10, color: "var(--text-muted)",
                      fontFamily: "var(--font-mono)" }}>DRAWDOWN (%)</div>
        <ResponsiveContainer width="100%" height={60}>
          <AreaChart data={history} margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%"   stopColor="#ff4757" stopOpacity={0.5} />
                <stop offset="100%" stopColor="#ff4757" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <XAxis dataKey="date" tick={axisTick} axisLine={false} tickLine={false} hide />
            <YAxis tick={axisTick} axisLine={false} tickLine={false} width={30}
                   tickFormatter={v => v.toFixed(0) + "%"} />
            <Tooltip contentStyle={ttStyle}
                     formatter={v => [v.toFixed(2) + "%", "Drawdown"]} />
            <ReferenceLine y={15} stroke="rgba(255,71,87,0.4)" strokeDasharray="3 2"
                           label={{ value:"LIMIT", fill:"#ff4757", fontSize:8, position:"insideTopRight" }} />
            <Area type="monotone" dataKey="drawdown" stroke="#ff4757" strokeWidth={1.5}
                  fill="url(#ddGrad)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}
