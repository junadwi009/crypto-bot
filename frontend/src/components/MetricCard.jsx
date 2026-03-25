/**
 * MetricCard — kartu angka ringkas untuk dashboard.
 * Props: label, value, sub, color ("green"|"red"|"yellow"|"blue"|"default")
 */
export default function MetricCard({ label, value, sub, color = "default" }) {
  const valueColor = {
    green:   "#4ade80",
    red:     "#f87171",
    yellow:  "#fbbf24",
    blue:    "#60a5fa",
    default: "#e2e8f0",
  }[color];

  return (
    <div style={s.card}>
      <div style={s.label}>{label}</div>
      <div style={{ ...s.value, color: valueColor }}>{value}</div>
      {sub && <div style={s.sub}>{sub}</div>}
    </div>
  );
}

const s = {
  card: {
    background: "#161b27",
    border: "1px solid #1e2535",
    borderRadius: 10,
    padding: "14px 16px",
  },
  label: {
    fontSize: 11,
    color: "#64748b",
    letterSpacing: "0.05em",
    textTransform: "uppercase",
    marginBottom: 6,
  },
  value: {
    fontSize: 22,
    fontWeight: 700,
    letterSpacing: "-0.02em",
    lineHeight: 1,
    marginBottom: 4,
  },
  sub: {
    fontSize: 11,
    color: "#475569",
    marginTop: 4,
  },
};
