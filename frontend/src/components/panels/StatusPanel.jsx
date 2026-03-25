import { Card, StatusDot, Badge, MetricRow, BigMetric } from "../ui/index.jsx";
import { useStatus, useSummary, useClaudeUsage } from "../../hooks/useData.js";

function fmt(n, decimals = 2) {
  const v = parseFloat(n || 0);
  return (v >= 0 ? "+" : "") + "$" + Math.abs(v).toFixed(decimals);
}

export default function StatusPanel() {
  const { data: status  } = useStatus();
  const { data: summary } = useSummary();
  const { data: claude  } = useClaudeUsage();

  const capital   = parseFloat(status?.capital       || 213);
  const dailyPnl  = parseFloat(summary?.daily_pnl    || status?.daily_pnl || 0);
  const winRate   = parseFloat(summary?.win_rate      || 0);
  const maxDD     = parseFloat(summary?.max_drawdown  || 0);
  const trades    = summary?.total_trades || 0;
  const tier      = status?.tier || "seed";
  const mode      = status?.paper_trade ? "PAPER" : "LIVE";
  const botStatus = status?.status || "—";
  const cbTripped = status?.circuit_breaker?.tripped;
  const pairs     = status?.active_pairs || [];

  const tierColors = { seed: "yellow", growth: "blue", pro: "purple", elite: "green" };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>

      {/* Bot status card */}
      <Card>
        <div style={{ padding: "14px 16px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <StatusDot status={botStatus} />
              <span style={{ fontSize: 11, fontWeight: 700, fontFamily: "var(--font-mono)",
                              letterSpacing: "0.1em", color: "var(--text-primary)" }}>
                {botStatus.toUpperCase()}
              </span>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <Badge color={mode === "PAPER" ? "blue" : "red"}>{mode}</Badge>
              <Badge color={tierColors[tier] || "default"}>{tier.toUpperCase()}</Badge>
            </div>
          </div>

          {/* Capital big */}
          <div style={{ marginBottom: 4 }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)",
                          letterSpacing: "0.1em", marginBottom: 4 }}>TOTAL CAPITAL</div>
            <div style={{ fontSize: 28, fontWeight: 700, fontFamily: "var(--font-mono)",
                          letterSpacing: "-0.03em", color: "var(--text-primary)" }}>
              ${capital.toFixed(2)}
            </div>
          </div>

          {/* Pairs pills */}
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 10 }}>
            {pairs.map(p => (
              <span key={p} style={{
                padding: "2px 8px", borderRadius: 4,
                background: "var(--accent-dim)", color: "var(--accent)",
                fontSize: 10, fontWeight: 700, fontFamily: "var(--font-mono)",
              }}>{p}</span>
            ))}
          </div>
        </div>
      </Card>

      {/* Metrics */}
      <Card>
        <div style={{ padding: "10px 16px 4px", fontSize: 10, fontWeight: 700,
                      letterSpacing: "0.12em", textTransform: "uppercase",
                      color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
          PERFORMANCE
        </div>
        <MetricRow label="Daily PnL"   value={fmt(dailyPnl)}
                   color={dailyPnl >= 0 ? "var(--green)" : "var(--red)"} />
        <MetricRow label="Win Rate"    value={(winRate * 100).toFixed(1) + "%"}
                   color={winRate >= 0.6 ? "var(--green)" : winRate >= 0.5 ? "var(--yellow)" : "var(--red)"} />
        <MetricRow label="Total Trades" value={trades} />
        <MetricRow label="Max Drawdown" value={(maxDD * 100).toFixed(2) + "%"}
                   color={maxDD > 0.1 ? "var(--red)" : maxDD > 0.05 ? "var(--yellow)" : "var(--green)"} />
        <MetricRow label="Net PnL 7d"  value={fmt(summary?.net_pnl)}
                   color={(summary?.net_pnl || 0) >= 0 ? "var(--green)" : "var(--red)"} />
        <MetricRow label="Total Fees"  value={"$" + parseFloat(summary?.total_fees || 0).toFixed(2)}
                   color="var(--text-muted)" />
      </Card>

      {/* Circuit breaker */}
      <Card style={{ border: cbTripped ? "1px solid var(--red)" : "1px solid var(--border-soft)" }}>
        <div style={{ padding: "10px 16px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.1em",
                          textTransform: "uppercase", color: "var(--text-muted)",
                          fontFamily: "var(--font-mono)" }}>CIRCUIT BREAKER</span>
          <Badge color={cbTripped ? "red" : "green"}>{cbTripped ? "TRIPPED" : "NORMAL"}</Badge>
        </div>
        {cbTripped && (
          <div style={{ padding: "0 16px 10px", fontSize: 11, color: "var(--red)",
                        fontFamily: "var(--font-mono)" }}>
            {status?.circuit_breaker?.reason || "Drawdown limit exceeded"}
          </div>
        )}
      </Card>

      {/* Claude usage */}
      {claude && (
        <Card>
          <div style={{ padding: "10px 16px 4px", fontSize: 10, fontWeight: 700,
                        letterSpacing: "0.12em", textTransform: "uppercase",
                        color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
            CLAUDE BUDGET
          </div>
          <MetricRow label="Mode"        value={(claude.mode || "normal").toUpperCase()}
                     color={claude.mode === "normal" ? "var(--green)" : "var(--yellow)"} />
          <MetricRow label="Est. balance" value={"$" + parseFloat(claude.estimated_balance || 0).toFixed(2)}
                     color={parseFloat(claude.estimated_balance) < 5 ? "var(--red)" : "var(--text-primary)"} />
          <MetricRow label="Days left"   value={parseFloat(claude.days_remaining || 0).toFixed(1) + "d"} />
          <MetricRow label="Monthly cost" value={"$" + parseFloat(claude.monthly_cost_usd || 0).toFixed(2)} />
          <MetricRow label="Burn/day"    value={"$" + parseFloat(claude.burn_rate_per_day || 0).toFixed(2)} />
        </Card>
      )}
    </div>
  );
}
