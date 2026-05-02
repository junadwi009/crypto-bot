/**
 * frontend/src/api.js
 *
 * PATCHED 2026-05-02:
 * - Hapus authConfig() — endpoint /api/auth/config dihapus karena
 *   bocorin SHA-256 hash 6-digit PIN ke client (rainbow-table-able)
 * - Semua fetch sekarang pakai credentials:'include' agar session
 *   cookie httpOnly ikut terkirim
 * - Tambah login(pin), logout(), authCheck(), learningSummary()
 */

const BASE = import.meta.env.VITE_API_URL || "";

async function get(path) {
  const res = await fetch(BASE + path, {
    credentials: "include",
  });
  if (res.status === 401) {
    // Session expired → redirect to login
    window.dispatchEvent(new Event("auth:expired"));
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

async function post(path, body) {
  const res = await fetch(BASE + path, {
    method:      "POST",
    headers:     { "Content-Type": "application/json" },
    credentials: "include",
    body:        body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 429) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "too_many_attempts");
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `${res.status} ${path}`);
  }
  return res.json();
}

export const api = {
  // ── Auth ───────────────────────────────────────────────────
  login:      (pin) => post("/api/auth/login", { pin }),
  logout:     ()    => post("/api/auth/logout"),
  authCheck:  ()    => get("/api/auth/check"),

  // ── Status / portfolio ─────────────────────────────────────
  status:           ()           => get("/api/status"),
  health:           ()           => get("/health"),
  portfolioSummary: ()           => get("/api/portfolio/summary"),
  portfolioHistory: (days = 30)  => get(`/api/portfolio/history?days=${days}`),
  allocation:       ()           => get("/api/portfolio/allocation"),

  // ── Trades ────────────────────────────────────────────────
  openTrades:       ()           => get("/api/trades/open"),
  recentTrades:     (days = 14)  => get(`/api/trades/recent?days=${days}`),

  // ── Pairs ─────────────────────────────────────────────────
  pairs:            ()           => get("/api/pairs"),
  pairParams:       (pair)       => get(`/api/pairs/${pair.replace("/", "-")}/params`),

  // ── Claude ────────────────────────────────────────────────
  claudeUsage:      ()           => get("/api/claude/usage"),
  opusMemory:       (weeks = 8)  => get(`/api/opus/memory?weeks=${weeks}`),
  opusLatestActions: ()          => get("/api/opus/latest-actions"),

  // ── News / events ─────────────────────────────────────────
  recentNews:       (hours = 24) => get(`/api/news/recent?hours=${hours}`),
  recentEvents:     (hours = 48) => get(`/api/events/recent?hours=${hours}`),

  // ── Infra ─────────────────────────────────────────────────
  infraFund:        ()           => get("/api/infra/fund"),

  // ── Market data ───────────────────────────────────────────
  price:            (pair)       => get(`/api/price/${pair.replace("/", "-")}`),
  tickerAll:        ()           => get("/api/ticker/all"),
  ohlcv:            (pair, interval = "15", limit = 80) =>
    get(`/api/ohlcv/${pair.replace("/", "-")}?interval=${interval}&limit=${limit}`),

  // ── Learning summary (NEW) ────────────────────────────────
  learningSummary:  ()           => get("/api/learning/summary"),
};
