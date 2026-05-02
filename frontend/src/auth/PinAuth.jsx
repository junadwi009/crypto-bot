/**
 * frontend/src/auth/PinAuth.jsx
 *
 * PATCHED 2026-05-02 (SECURITY):
 * - HAPUS client-side hash comparison. PIN dikirim ke server lewat
 *   POST /api/auth/login. Server verifikasi & set httpOnly cookie.
 * - Server cookie expire 4 jam. Client hanya track lastMove untuk
 *   idle warning UI.
 * - Idle warning tetap di client (UX) tapi enforce-nya server-side.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "../api";

const SESSION_TTL  = 4 * 60 * 60 * 1000;
const IDLE_TIMEOUT = 30 * 60 * 1000;
const WARN_SECS    = 30;

/* ── PIN input 6 kotak ──────────────────────────────────── */
function PinInput({ onSubmit, error, disabled }) {
  const [digits, setDigits] = useState(["","","","","",""]);
  const refs = Array.from({ length: 6 }, () => useRef());

  useEffect(() => {
    if (error) {
      setDigits(["","","","","",""]);
      setTimeout(() => refs[0].current?.focus(), 50);
    }
  }, [error]);

  const handleKey = (i, e) => {
    if (disabled) return;
    if (e.key === "Backspace") {
      if (digits[i]) { const n=[...digits]; n[i]=""; setDigits(n); }
      else if (i > 0) refs[i-1].current.focus();
      return;
    }
    if (!/^\d$/.test(e.key)) return;
    const n=[...digits]; n[i]=e.key; setDigits(n);
    if (i < 5) refs[i+1].current.focus();
    if (i === 5) { const pin=[...n.slice(0,5),e.key].join(""); if(pin.length===6) onSubmit(pin); }
  };

  const handlePaste = (e) => {
    if (disabled) return;
    const t = e.clipboardData.getData("text").replace(/\D/g,"").slice(0,6);
    if (t.length===6) { setDigits(t.split("")); refs[5].current.focus(); onSubmit(t); }
  };

  return (
    <div style={{ display:"flex", gap:10, justifyContent:"center" }}>
      {digits.map((d,i) => (
        <input key={i} ref={refs[i]} type="password" inputMode="numeric"
          maxLength={1} value={d} onChange={()=>{}}
          onKeyDown={e=>handleKey(i,e)} onPaste={handlePaste}
          autoFocus={i===0} disabled={disabled}
          style={{
            width:44, height:52, textAlign:"center", fontSize:22, fontWeight:700,
            fontFamily:"monospace",
            background: d ? "rgba(0,212,170,0.12)" : "rgba(255,255,255,0.04)",
            border:`1.5px solid ${error?"#ff4757":d?"rgba(0,212,170,0.5)":"rgba(255,255,255,0.1)"}`,
            borderRadius:8, color:"#e8edf3", outline:"none", transition:"all 0.15s",
          }}
        />
      ))}
    </div>
  );
}

/* ── Idle warning overlay ───────────────────────────────── */
function IdleWarning({ secondsLeft, onStayActive }) {
  return (
    <div style={{
      position:"fixed", inset:0, zIndex:9999,
      background:"rgba(8,12,16,0.88)",
      backdropFilter:"blur(8px)",
      display:"flex", alignItems:"center", justifyContent:"center",
    }}>
      <div style={{
        background:"rgba(22,30,40,0.98)",
        border:"1px solid rgba(255,211,42,0.3)",
        borderRadius:16, padding:"32px 36px",
        textAlign:"center", width:300,
        boxShadow:"0 24px 64px rgba(0,0,0,0.6)",
      }}>
        <div style={{ fontSize:32, marginBottom:8 }}>⏱</div>
        <div style={{ fontSize:15, fontWeight:700, color:"#e8edf3", marginBottom:6 }}>
          Sesi akan berakhir
        </div>
        <div style={{ fontSize:12, color:"#8b98a8", marginBottom:20 }}>
          Tidak ada aktivitas terdeteksi
        </div>
        <div style={{
          fontSize:44, fontWeight:900, fontFamily:"monospace",
          color: secondsLeft <= 10 ? "#ff4757" : "#ffd32a",
          marginBottom:20, letterSpacing:"0.05em",
        }}>
          {secondsLeft}
        </div>
        <button onClick={onStayActive} style={{
          width:"100%", padding:"10px",
          background:"rgba(0,212,170,0.12)",
          border:"1px solid rgba(0,212,170,0.4)",
          borderRadius:8, color:"#00d4aa",
          fontSize:13, fontWeight:700,
          cursor:"pointer", fontFamily:"monospace",
          letterSpacing:"0.06em",
        }}>
          TETAP AKTIF
        </button>
      </div>
    </div>
  );
}

/* ── Main component ─────────────────────────────────────── */
export default function PinAuth({ children }) {
  const [authed,    setAuthed]    = useState(false);
  const [error,     setError]     = useState("");
  const [loading,   setLoading]   = useState(false);
  const [locked,    setLocked]    = useState(false);
  const [lockTimer, setLockTimer] = useState(0);
  const [idleLeft,  setIdleLeft]  = useState(null);
  const [bootChecking, setBootChecking] = useState(true);

  const idleTimer = useRef(null);
  const warnTimer = useRef(null);
  const warnCount = useRef(WARN_SECS);

  // Check session on mount
  useEffect(() => {
    api.authCheck()
      .then(r => setAuthed(!!r.authenticated))
      .catch(() => setAuthed(false))
      .finally(() => setBootChecking(false));

    const handler = () => setAuthed(false);
    window.addEventListener("auth:expired", handler);
    return () => window.removeEventListener("auth:expired", handler);
  }, []);

  const startWarn = () => {
    warnCount.current = WARN_SECS;
    setIdleLeft(WARN_SECS);
    warnTimer.current = setInterval(() => {
      warnCount.current -= 1;
      setIdleLeft(warnCount.current);
      if (warnCount.current <= 0) {
        clearInterval(warnTimer.current);
        api.logout().catch(() => {});
        setAuthed(false);
        setIdleLeft(null);
      }
    }, 1000);
  };

  const resetIdle = useCallback(() => {
    clearTimeout(idleTimer.current);
    clearInterval(warnTimer.current);
    setIdleLeft(null);
    idleTimer.current = setTimeout(startWarn, IDLE_TIMEOUT - WARN_SECS * 1000);
  }, []);

  const handleStayActive = () => {
    clearInterval(warnTimer.current);
    setIdleLeft(null);
    resetIdle();
  };

  useEffect(() => {
    if (!authed) {
      clearTimeout(idleTimer.current);
      clearInterval(warnTimer.current);
      return;
    }
    const events = ["mousemove","mousedown","keydown","touchstart","scroll","click"];
    const handler = () => { if (idleLeft === null) resetIdle(); };
    events.forEach(e => window.addEventListener(e, handler, { passive:true }));
    resetIdle();
    return () => {
      events.forEach(e => window.removeEventListener(e, handler));
      clearTimeout(idleTimer.current);
      clearInterval(warnTimer.current);
    };
  }, [authed, resetIdle]);

  useEffect(() => {
    if (!locked) return;
    const t = setInterval(() => {
      setLockTimer(s => {
        if (s <= 1) { clearInterval(t); setLocked(false); return 0; }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(t);
  }, [locked]);

  const handlePin = async (pin) => {
    if (locked || loading) return;
    setLoading(true); setError("");
    try {
      await api.login(pin);
      setAuthed(true);
    } catch (e) {
      const msg = String(e?.message || "");
      if (msg === "too_many_attempts") {
        setLocked(true);
        setLockTimer(3600);
        setError("Terlalu banyak percobaan. Server lock 1 jam.");
      } else {
        setError("PIN salah");
      }
    } finally {
      setLoading(false);
    }
  };

  // Loading auth check
  if (bootChecking) {
    return (
      <div style={{ minHeight:"100vh", background:"#080c10",
                    display:"flex", alignItems:"center", justifyContent:"center",
                    color:"#4a5568", fontFamily:"monospace", fontSize:12 }}>
        Checking session...
      </div>
    );
  }

  if (authed && idleLeft !== null) {
    return <>{children}<IdleWarning secondsLeft={idleLeft} onStayActive={handleStayActive} /></>;
  }
  if (authed) return children;

  return (
    <div style={{ minHeight:"100vh", background:"#080c10", display:"flex",
                  alignItems:"center", justifyContent:"center" }}>
      <div style={{ position:"fixed", inset:0, pointerEvents:"none",
        backgroundImage:"radial-gradient(ellipse at 50% 0%, rgba(0,212,170,0.06) 0%, transparent 70%)" }} />
      <div style={{
        width:340, background:"rgba(22,30,40,0.95)",
        border:"1px solid rgba(255,255,255,0.08)",
        borderRadius:16, padding:"36px 32px",
        boxShadow:"0 24px 64px rgba(0,0,0,0.6)", position:"relative",
      }}>
        <div style={{ textAlign:"center", marginBottom:28 }}>
          <div style={{
            width:48, height:48, borderRadius:12,
            background:"linear-gradient(135deg, #00d4aa, #00a882)",
            display:"flex", alignItems:"center", justifyContent:"center",
            fontSize:22, fontWeight:900, color:"#080c10",
            margin:"0 auto 12px", boxShadow:"0 8px 24px rgba(0,212,170,0.3)",
          }}>C</div>
          <div style={{ fontSize:18, fontWeight:700, color:"#e8edf3" }}>CryptoBot</div>
          <div style={{ fontSize:12, color:"#4a5568", marginTop:4, fontFamily:"monospace" }}>
            DASHBOARD ACCESS
          </div>
        </div>

        <div style={{ height:1, background:"rgba(255,255,255,0.06)", marginBottom:24 }} />
        <div style={{ fontSize:12, color:"#8b98a8", textAlign:"center", marginBottom:20 }}>
          Masukkan PIN 6 digit
        </div>

        {locked ? (
          <div style={{ textAlign:"center", padding:16,
                        background:"rgba(255,71,87,0.08)",
                        border:"1px solid rgba(255,71,87,0.2)", borderRadius:8 }}>
            <div style={{ fontSize:13, color:"#ff4757", marginBottom:6 }}>Server-locked</div>
            <div style={{ fontSize:32, fontWeight:700, fontFamily:"monospace", color:"#ff4757" }}>
              {Math.floor(lockTimer/60)}:{String(lockTimer%60).padStart(2,"0")}
            </div>
          </div>
        ) : (
          <PinInput onSubmit={handlePin} error={!!error} disabled={loading} />
        )}

        {error && !locked && (
          <div style={{ marginTop:14, textAlign:"center", fontSize:12,
                        color:"#ff4757", fontFamily:"monospace" }}>
            {error}
          </div>
        )}
        {loading && (
          <div style={{ marginTop:14, textAlign:"center", fontSize:11,
                        color:"#4a5568", fontFamily:"monospace" }}>
            Verifying...
          </div>
        )}

        <div style={{ marginTop:28, paddingTop:16,
                      borderTop:"1px solid rgba(255,255,255,0.05)",
                      textAlign:"center", fontSize:10, color:"#2d3748",
                      fontFamily:"monospace", letterSpacing:"0.06em" }}>
          AUTO-LOGOUT 30MIN IDLE · SERVER VERIFIED
        </div>
      </div>
    </div>
  );
}
