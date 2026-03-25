import { Card, Badge, Empty } from "../ui/index.jsx";
import { useNews } from "../../hooks/useData.js";

function sentimentBadge(v) {
  const n = parseFloat(v || 0);
  if (n >  0.3) return { color: "green",  label: "BULL" };
  if (n < -0.3) return { color: "red",    label: "BEAR" };
  return              { color: "default", label: "NEUT" };
}

function actionBadge(a) {
  const map = {
    opportunity: "green",
    hold:        "default",
    reduce_risk: "yellow",
    close:       "red",
  };
  return map[a] || "default";
}

export default function NewsPanel() {
  const { data } = useNews();
  const news = data?.news || [];

  return (
    <Card style={{ display: "flex", flexDirection: "column" }}>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "12px 16px", borderBottom: "1px solid var(--border-dim)",
      }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.12em",
                        textTransform: "uppercase", color: "var(--text-muted)",
                        fontFamily: "var(--font-mono)" }}>NEWS FEED</span>
        <span style={{ fontSize: 10, color: "var(--text-muted)",
                        fontFamily: "var(--font-mono)" }}>{news.length} items</span>
      </div>

      <div style={{ maxHeight: 320, overflowY: "auto" }}>
        {news.length === 0 ? <Empty text="No recent news" /> : news.map((item, i) => {
          const sb    = sentimentBadge(item.haiku_sentiment);
          const ab    = item.sonnet_action;
          const pairs = item.pairs_mentioned || [];
          const time  = item.published_at
            ? new Date(item.published_at).toLocaleTimeString("id-ID", { hour:"2-digit", minute:"2-digit" })
            : "";

          return (
            <div key={i} style={{
              padding: "9px 16px",
              borderBottom: "1px solid var(--border-dim)",
              transition: "background 0.1s",
            }}
            onMouseEnter={e => e.currentTarget.style.background = "var(--bg-elevated)"}
            onMouseLeave={e => e.currentTarget.style.background = "transparent"}
            >
              <div style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 5 }}>
                {/* Sentiment bar */}
                <div style={{
                  width: 3, borderRadius: 2, flexShrink: 0, alignSelf: "stretch",
                  background: sb.color === "green" ? "var(--green)" :
                              sb.color === "red"   ? "var(--red)"   : "var(--border-mid)",
                  minHeight: 16,
                }} />
                <span style={{ fontSize: 11, color: "var(--text-primary)", lineHeight: 1.5, flex: 1 }}>
                  {item.headline}
                </span>
              </div>

              <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                <Badge color={sb.color} size="xs">{sb.label}</Badge>
                {ab && <Badge color={actionBadge(ab)} size="xs">{ab.toUpperCase()}</Badge>}
                {pairs.map(p => (
                  <span key={p} style={{
                    fontSize: 9, padding: "1px 5px", borderRadius: 3,
                    background: "var(--accent-dim)", color: "var(--accent)",
                    fontFamily: "var(--font-mono)", fontWeight: 700,
                  }}>{p.split("/")[0]}</span>
                ))}
                <span style={{
                  marginLeft: "auto", fontSize: 10, color: "var(--text-muted)",
                  fontFamily: "var(--font-mono)",
                }}>{item.source} · {time}</span>
              </div>

              {/* Relevance bar */}
              {item.haiku_relevance != null && (
                <div style={{ marginTop: 6, height: 2, background: "var(--border-dim)", borderRadius: 1 }}>
                  <div style={{
                    height: "100%", borderRadius: 1,
                    width: (parseFloat(item.haiku_relevance) * 100) + "%",
                    background: "var(--accent)",
                    opacity: 0.5,
                  }} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
