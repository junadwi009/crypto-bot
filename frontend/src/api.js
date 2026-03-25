/**
 * api.js
 * Semua fetch call ke FastAPI backend.
 * Base URL otomatis — di dev pakai vite proxy, di prod pakai same origin.
 */

const BASE = "";

async function get(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

export const api = {
  status:          () => get("/api/status"),
  healthFull:      () => get("/api/health/full"),

  portfolioHistory: (days = 30) => get(`/api/portfolio/history?days=${days}`),
  portfolioSummary: ()          => get("/api/portfolio/summary"),

  openTrades:      ()          => get("/api/trades/open"),
  recentTrades:    (days = 7)  => get(`/api/trades/recent?days=${days}`),

  pairs:           ()          => get("/api/pairs"),
  pairParams:      (pair)      => get(`/api/pairs/${pair.replace("/", "-")}/params`),
  pairBacktest:    (pair)      => get(`/api/pairs/${pair.replace("/", "-")}/backtest`),

  claudeUsage:     ()          => get("/api/claude/usage"),
  opusMemory:      (weeks = 4) => get(`/api/opus/memory?weeks=${weeks}`),
  recentNews:      (hours = 24)=> get(`/api/news/recent?hours=${hours}`),

  infraFund:       ()          => get("/api/infra/fund"),
  recentEvents:    (hours = 24)=> get(`/api/events/recent?hours=${hours}`),
};
