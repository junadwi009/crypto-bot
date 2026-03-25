import { useState, useEffect } from "react";
import PairCard   from "../components/PairCard.jsx";
import MetricCard from "../components/MetricCard.jsx";
import { api }    from "../api.js";

export default function Portfolio() {
  const [pairs,  setPairs]  = useState([]);
  const [infra,  setInfra]  = useState(null);
  const [status, setStatus] = useState(null);
  const [loading,setLoading]= useState(true);

  useEffect(() => {
    Promise.allSettled([
      api.pairs(),
      api.infraFund(),
      api.status(),
    ]).then(([p, inf, st]) => {
      if (p.status   === "fulfilled") setPairs(p.value?.pairs ?? []);
      if (inf.status === "fulfilled") setInfra(inf.value);
      if (st.status  === "fulfilled") setStatus(st.value);
      setLoading(false);
    });
  }, []);

  const activePairs   = pairs.filter(p => p.active);
  const inactivePairs = pairs.filter(p => !p.active);
  const capital       = parseFloat(status?.capital ?? 0);
  const infraBal      = parseFloat(infra?.current_balance ?? 0);
  const txns          = infra?.transactions ?? [];

  if (loading) return <div style={s.loading}>Memuat portfolio...</div>;

  return (
    <div>
      <h1 style={s.title}>Portfolio</h1>
      <p style={s.desc}>Status pair dan alokasi modal</p>

      {/* Summary */}
      <div style={s.grid4}>
        <MetricCard label="Modal total"    value={`$${capital.toFixed(2)}`}    color="blue" />
        <MetricCard label="Pair aktif"     value={activePairs.length}           color="green" />
        <MetricCard label="Infra fund"     value={`$${infraBal.toFixed(2)}`}   color="yellow"
                    sub="15% dari profit" />
        <MetricCard label="Tier"           value={status?.tier?.toUpperCase()} color="default" />
      </div>

      {/* Active pairs */}
      <SectionHead title={`Pair aktif (${activePairs.length})`} />
      {activePairs.length === 0
        ? <Empty text="Belum ada pair aktif." />
        : (
          <div style={s.pairGrid}>
            {activePairs.map(p => <PairCard key={p.pair} pair={p} />)}
          </div>
        )
      }

      {/* Inactive pairs */}
      <SectionHead title={`Pair tidak aktif — butuh modal lebih besar (${inactivePairs.length})`} />
      <div style={s.pairGrid}>
        {inactivePairs.map(p => (
          <PairCard key={p.pair} pair={p} />
        ))}
      </div>

      {/* Infra fund transactions */}
      {txns.length > 0 && (
        <>
          <SectionHead title="Infra fund — transaksi terakhir" />
          <div style={s.card}>
            <table style={s.table}>
              <thead>
                <tr>
                  {["Tanggal", "Tipe", "Jumlah", "Keterangan", "Saldo"].map(h => (
                    <th key={h} style={s.th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {txns.slice(0, 10).map((t, i) => (
                  <tr key={i} style={i % 2 ? s.rowOdd : {}}>
                    <td style={s.td}>{t.txn_date}</td>
                    <td style={{ ...s.td,
                      color: t.type === "credit" ? "#4ade80" : "#f87171",
                      fontWeight: 600 }}>
                      {t.type}
                    </td>
                    <td style={{ ...s.td,
                      color: t.type === "credit" ? "#4ade80" : "#f87171" }}>
                      {t.type === "credit" ? "+" : "-"}${parseFloat(t.amount).toFixed(4)}
                    </td>
                    <td style={{ ...s.td, color: "#64748b" }}>{t.description}</td>
                    <td style={s.td}>${parseFloat(t.balance_after).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function SectionHead({ title }) {
  return <div style={s.sectionHead}>{title}</div>;
}

function Empty({ text }) {
  return <div style={s.empty}>{text}</div>;
}

const s = {
  title:   { fontSize: 20, fontWeight: 700, color: "#e2e8f0", marginBottom: 4 },
  desc:    { fontSize: 13, color: "#64748b", marginBottom: 24 },
  loading: { fontSize: 13, color: "#64748b", padding: "40px 0" },
  grid4: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
    gap: 12, marginBottom: 24,
  },
  sectionHead: {
    fontSize: 12, fontWeight: 600, color: "#94a3b8",
    letterSpacing: "0.05em", textTransform: "uppercase",
    marginBottom: 12, marginTop: 4,
  },
  pairGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
    gap: 12, marginBottom: 24,
  },
  card: {
    background: "#161b27", border: "1px solid #1e2535",
    borderRadius: 10, padding: "14px 16px", marginBottom: 24,
    overflowX: "auto",
  },
  table: { width: "100%", borderCollapse: "collapse", fontSize: 12 },
  th: {
    padding: "6px 10px", textAlign: "left",
    fontSize: 10, color: "#64748b", letterSpacing: "0.05em",
    textTransform: "uppercase", borderBottom: "1px solid #1e2535",
  },
  td:     { padding: "8px 10px", color: "#cbd5e1" },
  rowOdd: { background: "#0d111a" },
  empty:  { fontSize: 12, color: "#475569", marginBottom: 20 },
};
