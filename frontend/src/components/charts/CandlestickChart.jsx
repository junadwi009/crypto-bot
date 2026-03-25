/**
 * CandlestickChart.jsx
 * Full-pro chart: Candlestick + Bollinger Bands + Volume + RSI + MACD
 * Built with Recharts — no external charting lib needed.
 * Data is simulated from the bot's last known price + indicators.
 */
import { useState, useEffect } from "react";
import {
  ComposedChart, BarChart, Bar,
  Line, Area, XAxis, YAxis, Tooltip,
  CartesianGrid, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { Card, SectionTitle, Tabs, Badge } from "../ui/index.jsx";
import { api } from "../../api.js";

/* ── Fake OHLCV generator from last price ─────────────────── */
function generateCandles(basePrice = 84000, count = 60) {
  const candles = [];
  let price = basePrice * 0.92;
  for (let i = 0; i < count; i++) {
    const change   = (Math.random() - 0.48) * price * 0.012;
    const open     = price;
    const close    = price + change;
    const high     = Math.max(open, close) * (1 + Math.random() * 0.008);
    const low      = Math.min(open, close) * (1 - Math.random() * 0.008);
    const volume   = Math.random() * 800 + 200;
    const time     = new Date(Date.now() - (count - i) * 15 * 60000);
    candles.push({
      time: time.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" }),
      open: +open.toFixed(2),
      high: +high.toFixed(2),
      low:  +low.toFixed(2),
      close: +close.toFixed(2),
      volume: +volume.toFixed(0),
      isUp: close >= open,
    });
    price = close;
  }
  return candles;
}

/* ── Calculate indicators ─────────────────────────────────── */
function calcIndicators(candles) {
  const closes = candles.map(c => c.close);

  // SMA
  const sma20 = closes.map((_, i) => {
    if (i < 19) return null;
    return closes.slice(i - 19, i + 1).reduce((a, b) => a + b, 0) / 20;
  });

  // Bollinger Bands
  const bb = closes.map((_, i) => {
    if (i < 19) return { upper: null, lower: null, mid: null };
    const slice = closes.slice(i - 19, i + 1);
    const mean  = slice.reduce((a, b) => a + b, 0) / 20;
    const std   = Math.sqrt(slice.reduce((a, b) => a + (b - mean) ** 2, 0) / 20);
    return { upper: +(mean + 2 * std).toFixed(2), lower: +(mean - 2 * std).toFixed(2), mid: +mean.toFixed(2) };
  });

  // RSI
  const rsi = closes.map((_, i) => {
    if (i < 14) return null;
    const gains  = [];
    const losses = [];
    for (let j = i - 13; j <= i; j++) {
      const d = closes[j] - closes[j - 1];
      gains.push(d > 0 ? d : 0);
      losses.push(d < 0 ? -d : 0);
    }
    const ag = gains.reduce((a, b) => a + b, 0) / 14;
    const al = losses.reduce((a, b) => a + b, 0) / 14;
    if (al === 0) return 100;
    return +(100 - 100 / (1 + ag / al)).toFixed(2);
  });

  // MACD
  const ema = (arr, n) => {
    const result = Array(arr.length).fill(null);
    const k = 2 / (n + 1);
    let prev = null;
    for (let i = 0; i < arr.length; i++) {
      if (prev === null) { result[i] = arr[i]; prev = arr[i]; }
      else { result[i] = arr[i] * k + prev * (1 - k); prev = result[i]; }
    }
    return result;
  };
  const ema12   = ema(closes, 12);
  const ema26   = ema(closes, 26);
  const macdLine = ema12.map((v, i) => v && ema26[i] ? +(v - ema26[i]).toFixed(2) : null);
  const sigLine  = ema(macdLine.map(v => v || 0), 9);
  const histogram = macdLine.map((v, i) => v !== null ? +(v - sigLine[i]).toFixed(2) : null);

  return candles.map((c, i) => ({
    ...c,
    sma20:     sma20[i],
    bbUpper:   bb[i].upper,
    bbLower:   bb[i].lower,
    bbMid:     bb[i].mid,
    rsi:       rsi[i],
    macd:      macdLine[i],
    signal:    sigLine[i] ? +sigLine[i].toFixed(2) : null,
    histogram: histogram[i],
  }));
}

/* ── Custom candlestick bar ───────────────────────────────── */
function CandleBar(props) {
  const { x, y, width, height, payload } = props;
  if (!payload) return null;
  const { open, high, low, close, isUp } = payload;
  const fill   = isUp ? "#00d4aa" : "#ff4757";
  const bodyH  = Math.abs(height) || 1;
  const bodyY  = isUp ? y : y + height;
  const scaleY = props.yAxis?.scale;
  if (!scaleY) return null;
  const hiY = scaleY(high);
  const loY = scaleY(low);
  const cx  = x + width / 2;

  return (
    <g>
      <line x1={cx} x2={cx} y1={hiY} y2={loY} stroke={fill} strokeWidth={1} />
      <rect x={x + 1} y={bodyY} width={Math.max(width - 2, 1)} height={bodyH}
            fill={fill} fillOpacity={isUp ? 0.9 : 0.85} />
    </g>
  );
}

/* ── Tooltip ──────────────────────────────────────────────── */
const CustomTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  return (
    <div style={{
      background: "var(--bg-elevated)", border: "1px solid var(--border-mid)",
      borderRadius: 8, padding: "10px 14px", fontSize: 11,
      fontFamily: "var(--font-mono)", color: "var(--text-primary)",
      minWidth: 160,
    }}>
      <div style={{ marginBottom: 6, color: "var(--text-secondary)" }}>{d.time}</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "3px 12px" }}>
        <span style={{ color: "var(--text-muted)" }}>O</span><span>{d.open?.toLocaleString()}</span>
        <span style={{ color: "var(--text-muted)" }}>H</span><span style={{ color: "#00d4aa" }}>{d.high?.toLocaleString()}</span>
        <span style={{ color: "var(--text-muted)" }}>L</span><span style={{ color: "#ff4757" }}>{d.low?.toLocaleString()}</span>
        <span style={{ color: "var(--text-muted)" }}>C</span>
        <span style={{ color: d.isUp ? "#00d4aa" : "#ff4757", fontWeight: 700 }}>{d.close?.toLocaleString()}</span>
        <span style={{ color: "var(--text-muted)" }}>Vol</span><span>{d.volume?.toLocaleString()}</span>
        {d.rsi !== null && <><span style={{ color: "var(--text-muted)" }}>RSI</span><span style={{ color: d.rsi > 70 ? "#ff4757" : d.rsi < 30 ? "#00d4aa" : "var(--text-primary)" }}>{d.rsi}</span></>}
      </div>
    </div>
  );
};

/* ── Main component ───────────────────────────────────────── */
export default function CandlestickChart({ pair = "BTC/USDT", currentPrice = 84000 }) {
  const [tf,     setTf]     = useState("15m");
  const [data,   setData]   = useState([]);
  const [tab,    setTab]    = useState("price");

  useEffect(() => {
    const raw = generateCandles(currentPrice, 80);
    setData(calcIndicators(raw));
  }, [currentPrice, tf]);

  const last   = data[data.length - 1] || {};
  const isUp   = last.close >= last.open;
  const change = data.length > 1
    ? ((last.close - data[0].close) / data[0].close * 100).toFixed(2)
    : "0.00";

  const axisTick = { fontSize: 9, fill: "#4a5568", fontFamily: "var(--font-mono)" };
  const grid     = <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />;

  return (
    <Card style={{ display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "12px 16px", borderBottom: "1px solid var(--border-dim)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 15, fontWeight: 700, fontFamily: "var(--font-mono)" }}>{pair}</span>
          <span style={{
            fontSize: 18, fontWeight: 700, fontFamily: "var(--font-mono)",
            color: isUp ? "var(--green)" : "var(--red)",
          }}>
            {last.close?.toLocaleString() || "—"}
          </span>
          <Badge color={parseFloat(change) >= 0 ? "green" : "red"}>
            {parseFloat(change) >= 0 ? "+" : ""}{change}%
          </Badge>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {["5m","15m","1h","4h"].map(t => (
            <button key={t} onClick={() => setTf(t)} style={{
              padding: "3px 8px", borderRadius: 4, border: "none",
              background: tf === t ? "var(--accent-dim)" : "transparent",
              color: tf === t ? "var(--accent)" : "var(--text-muted)",
              fontSize: 10, fontWeight: 600, cursor: "pointer",
              fontFamily: "var(--font-mono)",
            }}>{t}</button>
          ))}
        </div>
      </div>

      {/* Sub-tabs */}
      <div style={{ display: "flex", gap: 2, padding: "8px 16px 0", borderBottom: "1px solid var(--border-dim)" }}>
        {[
          { key: "price",  label: "PRICE + BB" },
          { key: "volume", label: "VOLUME" },
          { key: "rsi",    label: "RSI" },
          { key: "macd",   label: "MACD" },
        ].map(t => (
          <button key={t.key} onClick={() => setTab(t.key)} style={{
            padding: "4px 10px", border: "none",
            borderBottom: tab === t.key ? "2px solid var(--accent)" : "2px solid transparent",
            background: "transparent",
            color: tab === t.key ? "var(--accent)" : "var(--text-muted)",
            fontSize: 10, fontWeight: 700, cursor: "pointer",
            fontFamily: "var(--font-mono)", letterSpacing: "0.08em",
          }}>{t.label}</button>
        ))}
      </div>

      {/* PRICE + BB */}
      {tab === "price" && (
        <div style={{ padding: "12px 4px 8px" }}>
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              {grid}
              <XAxis dataKey="time" tick={axisTick} axisLine={false} tickLine={false}
                     interval={Math.floor(data.length / 6)} />
              <YAxis tick={axisTick} axisLine={false} tickLine={false} width={60}
                     tickFormatter={v => v.toLocaleString()} domain={["auto","auto"]} />
              <Tooltip content={<CustomTooltip />} />

              {/* BB fill */}
              <Area type="monotone" dataKey="bbUpper" fill="rgba(0,212,170,0.03)"
                    stroke="rgba(0,212,170,0.3)" strokeWidth={1} strokeDasharray="3 3" dot={false} />
              <Area type="monotone" dataKey="bbLower" fill="rgba(0,212,170,0.03)"
                    stroke="rgba(0,212,170,0.3)" strokeWidth={1} strokeDasharray="3 3" dot={false} />
              <Line type="monotone" dataKey="bbMid" stroke="rgba(0,212,170,0.4)"
                    strokeWidth={1} strokeDasharray="5 3" dot={false} />
              <Line type="monotone" dataKey="sma20" stroke="#ffd32a"
                    strokeWidth={1} dot={false} opacity={0.7} />

              {/* Candles as bar */}
              <Bar dataKey="close" shape={<CandleBar />} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>

          {/* Legend */}
          <div style={{ display: "flex", gap: 16, padding: "4px 12px", fontSize: 10,
                        fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
            <span style={{ color: "rgba(0,212,170,0.6)" }}>— BB</span>
            <span style={{ color: "#ffd32a" }}>— SMA20</span>
            <span style={{ color: "var(--green)" }}>▲ Bull</span>
            <span style={{ color: "var(--red)" }}>▼ Bear</span>
          </div>
        </div>
      )}

      {/* VOLUME */}
      {tab === "volume" && (
        <div style={{ padding: "12px 4px 8px" }}>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              {grid}
              <XAxis dataKey="time" tick={axisTick} axisLine={false} tickLine={false}
                     interval={Math.floor(data.length / 6)} />
              <YAxis tick={axisTick} axisLine={false} tickLine={false} width={50}
                     tickFormatter={v => v >= 1000 ? (v/1000).toFixed(0)+"K" : v} />
              <Tooltip formatter={(v) => [v.toLocaleString(), "Volume"]}
                       contentStyle={{ background:"var(--bg-elevated)", border:"1px solid var(--border-mid)",
                                        borderRadius:8, fontSize:11, fontFamily:"var(--font-mono)" }} />
              <Bar dataKey="volume" isAnimationActive={false}
                   fill="#00d4aa"
                   label={false}
                   radius={[2,2,0,0]}>
                {data.map((entry, index) => (
                  <rect key={index} fill={entry.isUp ? "rgba(0,212,170,0.5)" : "rgba(255,71,87,0.5)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* RSI */}
      {tab === "rsi" && (
        <div style={{ padding: "12px 4px 8px" }}>
          <div style={{ padding: "0 16px 8px", fontSize: 11, color: "var(--text-secondary)",
                        fontFamily: "var(--font-mono)", display: "flex", gap: 16 }}>
            <span>RSI(14): <span style={{ color: last.rsi > 70 ? "var(--red)" : last.rsi < 30 ? "var(--green)" : "var(--text-primary)", fontWeight: 700 }}>{last.rsi || "—"}</span></span>
            <Badge color={last.rsi > 70 ? "red" : last.rsi < 30 ? "green" : "default"}>
              {last.rsi > 70 ? "OVERBOUGHT" : last.rsi < 30 ? "OVERSOLD" : "NEUTRAL"}
            </Badge>
          </div>
          <ResponsiveContainer width="100%" height={160}>
            <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              {grid}
              <XAxis dataKey="time" tick={axisTick} axisLine={false} tickLine={false}
                     interval={Math.floor(data.length / 6)} />
              <YAxis tick={axisTick} axisLine={false} tickLine={false} width={30}
                     domain={[0, 100]} ticks={[0,30,50,70,100]} />
              <Tooltip contentStyle={{ background:"var(--bg-elevated)", border:"1px solid var(--border-mid)",
                                        borderRadius:8, fontSize:11, fontFamily:"var(--font-mono)" }} />
              <ReferenceLine y={70} stroke="rgba(255,71,87,0.4)"   strokeDasharray="4 2" label={{ value:"OB", fill:"#ff4757", fontSize:9 }} />
              <ReferenceLine y={30} stroke="rgba(0,212,170,0.4)"   strokeDasharray="4 2" label={{ value:"OS", fill:"#00d4aa", fontSize:9 }} />
              <ReferenceLine y={50} stroke="rgba(255,255,255,0.08)" strokeDasharray="4 2" />
              <Area type="monotone" dataKey="rsi" stroke="#a78bfa" strokeWidth={2}
                    fill="rgba(167,139,250,0.08)" dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* MACD */}
      {tab === "macd" && (
        <div style={{ padding: "12px 4px 8px" }}>
          <div style={{ padding: "0 16px 8px", fontSize: 11, color: "var(--text-secondary)",
                        fontFamily: "var(--font-mono)", display: "flex", gap: 16 }}>
            <span>MACD: <span style={{ color: last.macd > 0 ? "var(--green)" : "var(--red)", fontWeight: 700 }}>
              {last.macd?.toFixed(2) || "—"}
            </span></span>
            <span>Signal: <span style={{ color: "var(--yellow)" }}>{last.signal?.toFixed(2) || "—"}</span></span>
            <span>Hist: <span style={{ color: (last.histogram||0) > 0 ? "var(--green)" : "var(--red)" }}>
              {last.histogram?.toFixed(2) || "—"}
            </span></span>
          </div>
          <ResponsiveContainer width="100%" height={160}>
            <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              {grid}
              <XAxis dataKey="time" tick={axisTick} axisLine={false} tickLine={false}
                     interval={Math.floor(data.length / 6)} />
              <YAxis tick={axisTick} axisLine={false} tickLine={false} width={40}
                     domain={["auto","auto"]} />
              <Tooltip contentStyle={{ background:"var(--bg-elevated)", border:"1px solid var(--border-mid)",
                                        borderRadius:8, fontSize:11, fontFamily:"var(--font-mono)" }} />
              <ReferenceLine y={0} stroke="rgba(255,255,255,0.1)" />
              <Bar dataKey="histogram" isAnimationActive={false} radius={[1,1,0,0]}>
                {data.map((entry, i) => (
                  <rect key={i} fill={(entry.histogram||0) >= 0 ? "rgba(0,212,170,0.6)" : "rgba(255,71,87,0.6)"} />
                ))}
              </Bar>
              <Line type="monotone" dataKey="macd"   stroke="#00d4aa" strokeWidth={1.5} dot={false} />
              <Line type="monotone" dataKey="signal" stroke="#ffd32a" strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}
