/**
 * frontend/src/pages/CapitalPage.jsx
 *
 * Halaman Capital Management:
 * - List rekomendasi injection dari Opus yang masih pending
 * - Approve/reject langsung dari dashboard
 * - Manual inject (kalau user mau tambah modal tanpa nunggu Opus)
 * - Audit log auto-evolution: pair activated/deactivated, injection history
 */

import { useState, useEffect } from "react";
import { api } from "../api";

const C = {
  bg:        "var(--bg-void, #080c10)",
  card:      "rgba(22,30,40,0.95)",
  border:    "rgba(255,255,255,0.08)",
  text:      "#e8edf3",
  muted:     "#8b98a8",
  accent:    "#00d4aa",
  warn:      "#ffd32a",
  danger:    "#ff4757",
  pos:       "#00d4aa",
  neg:       "#ff4757",
};

export default function CapitalPage() {
  const [pending,    setPending]    = useState([]);
  const [auditLog,   setAuditLog]   = useState([]);
  const [allocation, setAllocation] = useState(null);
  const [loading,    setLoading]    = useState(true);
  const [actionMsg,  setActionMsg]  = useState("");
  const [manualOpen, setManualOpen] = useState(false);
  const [manualAmount, setManualAmount] = useState("");
  const [manualNote,   setManualNote]   = useState("");

  const loadAll = async () => {
    try {
      setLoading(true);
      const [pendData, auditData, allocData] = await Promise.all([
        api.pendingInjections().catch(() => ({ pending: [] })),
        api.autoEvolutionLog(30).catch(() => ({ actions: [] })),
        api.allocation().catch(() => null),
      ]);
      setPending(pendData.pending || []);
      setAuditLog(auditData.actions || []);
      setAllocation(allocData);
    } catch (e) {
      console.error("Failed to load capital page:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
    const t = setInterval(loadAll, 60000);
    return () => clearInterval(t);
  }, []);

  const handleApprove = async (id) => {
    if (!window.confirm("Approve injection ini? Capital tracking akan di-update.")) return;
    try {
      await api.approveInjection(id);
      setActionMsg("Injection approved. Refresh data...");
      await loadAll();
      setTimeout(() => setActionMsg(""), 3000);
    } catch (e) {
      setActionMsg(`Error: ${e.message}`);
    }
  };

  const handleReject = async (id) => {
    if (!window.confirm("Reject injection ini?")) return;
    try {
      await api.rejectInjection(id);
      setActionMsg("Injection rejected.");
      await loadAll();
      setTimeout(() => setActionMsg(""), 3000);
    } catch (e) {
      setActionMsg(`Error: ${e.message}`);
    }
  };

  const handleManualInject = async () => {
    const amt = parseFloat(manualAmount);
    if (!amt || amt <= 0 || amt > 10000) {
      setActionMsg("Amount harus 0–10000");
      return;
    }
    if (!window.confirm(`Inject $${amt.toFixed(2)} ke capital tracking?`)) return;
    try {
      const result = await api.manualInject(amt, manualNote);
      setActionMsg(
        `Injection sukses. Capital: $${result.previous_capital.toFixed(2)} → $${result.new_capital.toFixed(2)}`
      );
      setManualOpen(false);
      setManualAmount("");
      setManualNote("");
      await loadAll();
      setTimeout(() => setActionMsg(""), 5000);
    } catch (e) {
      setActionMsg(`Error: ${e.message}`);
    }
  };

  return (
    <div style={{ padding: 4, color: C.text }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>
        Capital Management
      </h1>
      <p style={{ fontSize: 13, color: C.muted, marginBottom: 20 }}>
        Kelola modal trading. Auto-evolving rules akan rekomendasi penambahan
        modal saat dianggap perlu — semua butuh persetujuan Anda.
      </p>

      {actionMsg && (
        <div style={{
          background: "rgba(0,212,170,0.1)",
          border: `1px solid ${C.accent}`,
          color: C.accent,
          padding: "10px 14px",
          borderRadius: 8,
          marginBottom: 16,
          fontSize: 13,
          fontFamily: "monospace",
        }}>
          {actionMsg}
        </div>
      )}

      {/* Allocation overview */}
      {allocation && (
        <Card title="Alokasi Saat Ini">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16 }}>
            <Stat label="Trading"  value={`$${allocation.trading.toFixed(2)}`} />
            <Stat label="Buffer"   value={`$${allocation.buffer.toFixed(2)}`} />
            <Stat label="Infra"    value={`$${allocation.infra.toFixed(2)}`} />
            <Stat label="Total"    value={`$${allocation.total.toFixed(2)}`} bold />
          </div>
        </Card>
      )}

      {/* Pending Opus recommendations */}
      <Card title={`Rekomendasi Opus (${pending.length} pending)`}>
        {loading && <div style={{ color: C.muted }}>Loading...</div>}
        {!loading && pending.length === 0 && (
          <div style={{ color: C.muted, fontSize: 13, fontStyle: "italic" }}>
            Tidak ada rekomendasi pending. Opus akan evaluasi tiap Senin 08:00 WIB.
          </div>
        )}
        {pending.map(item => (
          <div key={item.id} style={{
            background: "rgba(255,211,42,0.06)",
            border: `1px solid rgba(255,211,42,0.3)`,
            borderRadius: 8,
            padding: 14,
            marginTop: 12,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <div>
                <div style={{ fontSize: 11, color: C.muted, fontFamily: "monospace" }}>
                  ID: {item.id} · Recommended {new Date(item.recommended_at).toLocaleString("id-ID")}
                </div>
                <div style={{ fontSize: 24, fontWeight: 700, color: C.warn, marginTop: 4 }}>
                  ${item.amount.toFixed(2)}
                </div>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <Button color={C.accent} onClick={() => handleApprove(item.id)}>
                  Approve
                </Button>
                <Button color={C.danger} onClick={() => handleReject(item.id)}>
                  Reject
                </Button>
              </div>
            </div>
            <div style={{ fontSize: 13, marginTop: 8 }}>
              <strong>Alasan:</strong> {item.reason || "(tidak ada alasan diberikan)"}
            </div>
            <div style={{ fontSize: 13, marginTop: 6, color: C.muted }}>
              <strong>Expected impact:</strong> {item.expected_impact || "(tidak disebutkan)"}
            </div>
            <div style={{ fontSize: 11, marginTop: 8, color: C.muted, fontFamily: "monospace" }}>
              Expires: {new Date(item.expires_at).toLocaleDateString("id-ID")}
            </div>
          </div>
        ))}
      </Card>

      {/* Manual injection */}
      <Card title="Manual Injection">
        {!manualOpen ? (
          <button onClick={() => setManualOpen(true)} style={{
            background: "transparent",
            border: `1px dashed ${C.muted}`,
            color: C.muted,
            padding: "10px 16px",
            borderRadius: 8,
            cursor: "pointer",
            fontSize: 13,
            width: "100%",
          }}>
            + Tambah modal manual (tanpa nunggu Opus)
          </button>
        ) : (
          <div>
            <div style={{ marginBottom: 10 }}>
              <label style={{ fontSize: 12, color: C.muted, display: "block", marginBottom: 4 }}>
                Amount (USD)
              </label>
              <input
                type="number"
                value={manualAmount}
                onChange={e => setManualAmount(e.target.value)}
                placeholder="100"
                style={{
                  width: "100%",
                  padding: "10px",
                  background: "rgba(0,0,0,0.3)",
                  border: `1px solid ${C.border}`,
                  borderRadius: 8,
                  color: C.text,
                  fontSize: 14,
                  fontFamily: "monospace",
                }}
              />
            </div>
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: C.muted, display: "block", marginBottom: 4 }}>
                Note (opsional)
              </label>
              <input
                type="text"
                value={manualNote}
                onChange={e => setManualNote(e.target.value)}
                placeholder="Top-up Mei 2026"
                maxLength={200}
                style={{
                  width: "100%",
                  padding: "10px",
                  background: "rgba(0,0,0,0.3)",
                  border: `1px solid ${C.border}`,
                  borderRadius: 8,
                  color: C.text,
                  fontSize: 14,
                }}
              />
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <Button color={C.accent} onClick={handleManualInject}>Inject</Button>
              <Button color={C.muted} onClick={() => {
                setManualOpen(false);
                setManualAmount("");
                setManualNote("");
              }}>Batal</Button>
            </div>
            <div style={{ fontSize: 11, color: C.muted, marginTop: 10, fontStyle: "italic" }}>
              Catatan: Anda harus transfer dana ke akun Bybit secara terpisah.
              Tombol ini hanya update tracking di database.
            </div>
          </div>
        )}
      </Card>

      {/* Auto-evolution audit log */}
      <Card title={`Auto-Evolution Log (${auditLog.length} aksi 30 hari)`}>
        {auditLog.length === 0 && (
          <div style={{ color: C.muted, fontSize: 13, fontStyle: "italic" }}>
            Belum ada aksi otomatis. Tunggu Opus eval Senin 08:00 WIB.
          </div>
        )}
        {auditLog.map((evt, i) => (
          <LogRow key={i} evt={evt} />
        ))}
      </Card>
    </div>
  );
}

function Card({ title, children }) {
  return (
    <div style={{
      background: C.card,
      border: `1px solid ${C.border}`,
      borderRadius: 12,
      padding: 18,
      marginBottom: 16,
    }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: C.muted,
                    textTransform: "uppercase", letterSpacing: "0.06em",
                    marginBottom: 12 }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function Stat({ label, value, bold }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: C.muted, marginBottom: 4 }}>{label}</div>
      <div style={{
        fontSize: bold ? 22 : 18,
        fontWeight: bold ? 700 : 600,
        fontFamily: "monospace",
        color: bold ? C.accent : C.text,
      }}>
        {value}
      </div>
    </div>
  );
}

function Button({ color, onClick, children }) {
  return (
    <button onClick={onClick} style={{
      background: `${color}1a`,
      border: `1px solid ${color}`,
      color,
      padding: "8px 14px",
      borderRadius: 6,
      fontSize: 12,
      fontWeight: 700,
      cursor: "pointer",
      fontFamily: "monospace",
      letterSpacing: "0.04em",
    }}>
      {children}
    </button>
  );
}

function LogRow({ evt }) {
  const eventColors = {
    auto_pair_activated:           C.pos,
    auto_pair_deactivated:         C.warn,
    capital_injection_recommended: C.warn,
    capital_injection_approved:    C.pos,
    capital_injection_rejected:    C.muted,
    capital_injection_manual:      C.pos,
  };
  const color = eventColors[evt.event_type] || C.muted;
  const data = evt.data || {};

  return (
    <div style={{
      borderLeft: `2px solid ${color}`,
      paddingLeft: 12,
      paddingTop: 6,
      paddingBottom: 6,
      marginBottom: 8,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span style={{ fontSize: 11, color, fontFamily: "monospace",
                       textTransform: "uppercase", fontWeight: 700 }}>
          {evt.event_type.replace(/_/g, " ")}
        </span>
        <span style={{ fontSize: 11, color: C.muted, fontFamily: "monospace" }}>
          {new Date(evt.created_at).toLocaleString("id-ID")}
        </span>
      </div>
      <div style={{ fontSize: 13, marginTop: 4 }}>{evt.message}</div>
      {data.amount && (
        <div style={{ fontSize: 12, color: C.muted, marginTop: 2,
                      fontFamily: "monospace" }}>
          Amount: ${data.amount}
          {data.previous_capital && data.new_capital && (
            <> · ${data.previous_capital.toFixed(2)} → ${data.new_capital.toFixed(2)}</>
          )}
        </div>
      )}
      {data.pair && (
        <div style={{ fontSize: 12, color: C.muted, marginTop: 2,
                      fontFamily: "monospace" }}>
          Pair: {data.pair}
          {data.reason && ` · ${data.reason}`}
        </div>
      )}
    </div>
  );
}
