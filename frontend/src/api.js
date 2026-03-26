/**
 * api.js
 * Di development: proxy ke localhost:8000 via vite
 * Di production: pakai VITE_API_URL env var yang diisi URL backend Render
 */

const BASE = import.meta.env.VITE_API_URL || "";

async function get(path) {
  const res = await fetch(BASE + path);
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

export const api = {
  status:           ()           => get("/api/status"),
  health:           ()           => get("/health"),
  portfolioSummary: ()           => get("/api/portfolio/summary"),
  portfolioHistory: (days = 30)  => get(`/api/portfolio/history?days=${days}`),
  openTrades:       ()           => get("/api/trades/open"),
  recentTrades:     (days = 14)  => get(`/api/trades/recent?days=${days}`),
  pairs:            ()           => get("/api/pairs"),
  pairParams:       (pair)       => get(`/api/pairs/${pair.replace("/","-")}/params`),
  claudeUsage:      ()           => get("/api/claude/usage"),
  opusMemory:       (weeks = 8)  => get(`/api/opus/memory?weeks=${weeks}`),
  recentNews:       (hours = 24) => get(`/api/news/recent?hours=${hours}`),
  infraFund:        ()           => get("/api/infra/fund"),
  recentEvents:     (hours = 48) => get(`/api/events/recent?hours=${hours}`),
  authConfig:       ()           => get("/api/auth/config"),
};