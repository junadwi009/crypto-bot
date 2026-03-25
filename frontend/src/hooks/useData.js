import { useState, useEffect, useCallback } from "react";
import { api } from "../api.js";

export function useApi(fn, interval = 15000, deps = []) {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);

  const load = useCallback(async () => {
    try {
      const result = await fn();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, deps); // eslint-disable-line

  useEffect(() => {
    load();
    if (interval > 0) {
      const t = setInterval(load, interval);
      return () => clearInterval(t);
    }
  }, [load, interval]);

  return { data, loading, error, reload: load };
}

export function useStatus()          { return useApi(api.status,           10000); }
export function useSummary()         { return useApi(api.portfolioSummary,  15000); }
export function useHistory(days=30)  { return useApi(() => api.portfolioHistory(days), 60000, [days]); }
export function useOpenTrades()      { return useApi(api.openTrades,        10000); }
export function useRecentTrades(d=14){ return useApi(() => api.recentTrades(d), 30000, [d]); }
export function usePairs()           { return useApi(api.pairs,             30000); }
export function useClaudeUsage()     { return useApi(api.claudeUsage,       60000); }
export function useOpusMemory()      { return useApi(api.opusMemory,       120000); }
export function useNews()            { return useApi(api.recentNews,        30000); }
export function useEvents()          { return useApi(api.recentEvents,      20000); }
export function useInfra()           { return useApi(api.infraFund,         60000); }
