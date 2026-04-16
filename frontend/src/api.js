/**
 * frontend/src/api.js
 * Di development: proxy ke localhost:8000 via vite
 * Di production: pakai VITE_API_URL env var yang diisi URL backend Render
 *
 * UPDATED 2026-04-16:
 *   - Tambah price(), tickerAll(), ohlcv(), allocation(), opusLatestActions()
 */

const BASE = import.meta.env.VITE_API_URL || "";

async function get(path) {
  const res = await fetch(BASE + path);
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

export const api = {
  // ── Existing ──────────────────────────────────────────────
  status:           ()           => get("/api/status"),
  health:           ()           => get("/health"),
  portfolioSummary: ()           => get("/api/portfolio/summary"),
  portfolioHistory: (days = 30)  => get(`/api/portfolio/history?days=${days}`),
  openTrades:       ()           => get("/api/trades/open"),
  recentTrades:     (days = 14)  => get(`/api/trades/recent?days=${days}`),
  pairs:            ()           => get("/api/pairs"),
  pairParams:       (pair)       => get(`/api/pairs/${pair.replace("/", "-")}/params`),
  claudeUsage:      ()           => get("/api/claude/usage"),
  opusMemory:       (weeks = 8)  => get(`/api/opus/memory?weeks=${weeks}`),
  recentNews:       (hours = 24) => get(`/api/news/recent?hours=${hours}`),
  infraFund:        ()           => get("/api/infra/fund"),
  recentEvents:     (hours = 48) => get(`/api/events/recent?hours=${hours}`),
  authConfig:       ()           => get("/api/auth/config"),

  // ── New: market data ──────────────────────────────────────
  /** Harga realtime 1 pair dari Bybit. pair: "BTC-USDT" */
  price:            (pair)       => get(`/api/price/${pair.replace("/", "-")}`),

  /** Semua ticker sekaligus untuk tape — 1 request. */
  tickerAll:        ()           => get("/api/ticker/all"),

  /** OHLCV candle untuk chart. interval: "1","5","15","60","D" */
  ohlcv:            (pair, interval = "15", limit = 80) =>
    get(`/api/ohlcv/${pair.replace("/", "-")}?interval=${interval}&limit=${limit}`),

  // ── New: portfolio ────────────────────────────────────────
  /** Alokasi modal: trading/infra/buffer. */
  allocation:       ()           => get("/api/portfolio/allocation"),

  // ── New: opus ─────────────────────────────────────────────
  /** P0/P1 actions terbaru untuk sticky banner. */
  opusLatestActions: ()          => get("/api/opus/latest-actions"),
};