import { useEffect, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8080";
const REMEDIATION_BASE = import.meta.env.VITE_REMEDIATION_BASE || "http://localhost:8084";
const ML_BASE = import.meta.env.VITE_ML_BASE || "http://localhost:8083";

// Charte imposée (slide 9) : écriture #261E48, fond #E4E6F3, rectangles #A1A6D4.
// Les couleurs d'état sont volontairement hors charte : elles doivent rester
// lisibles à distance (Clip Chrono), c'est un choix assumé et documenté.
const STATE_STYLE = {
  IDLE: { label: "INIT", color: "#5b5875", text: "En attente" },
  NORMAL_TX: { label: "NORMAL", color: "#0f9d63", text: "Transactions nominales" },
  UNDER_ATTACK: { label: "SOUS ATTAQUE", color: "#c8102e", text: "Anomalie détectée par le ML" },
  REMEDIATING: { label: "REMEDIATION", color: "#d97706", text: "Playbook Ansible en cours" },
  RECOVERED_TX: { label: "RETABLI", color: "#0f9d63", text: "Transaction de preuve confirmée" },
  REMEDIATION_FAILED: { label: "ECHEC", color: "#c8102e", text: "Remédiation incomplète — action requise" },
};

function StatusBadge({ state }) {
  const style = STATE_STYLE[state] || STATE_STYLE.IDLE;
  return (
    <span
      className="px-3 py-1 rounded-full text-sm font-bold text-white"
      style={{ backgroundColor: style.color }}
    >
      {style.label}
    </span>
  );
}

function Card({ title, children, className = "" }) {
  return (
    <div className={`bg-card rounded-xl p-5 h-full ${className}`}>
      <h2 className="text-ink font-bold mb-3">{title}</h2>
      {children}
    </div>
  );
}

/**
 * Anneau de LEDs en SVG : miroir exact de l'objet physique.
 * Même état, même séquence, même cadence. Si l'ESP32 est débranché, la démo
 * reste complète — c'est la garantie anti-panne (cf. docs/hardware).
 */
function LedRing({ state, seq }) {
  const [phase, setPhase] = useState(0);
  const count = 24;

  useEffect(() => {
    const id = setInterval(() => setPhase((p) => (p + 1) % count), 120);
    return () => clearInterval(id);
  }, []);

  const colorFor = (index) => {
    switch (state) {
      case "UNDER_ATTACK":
        return (index * 2654435761 + seq) % 100 < 25 ? "#dc0000" : "#2a0000";
      case "REMEDIATING":
        return (index - phase + count) % count < 3 ? "#ffffff" : "#ff8c00";
      case "RECOVERED_TX":
        return "#0f9d63";
      case "REMEDIATION_FAILED":
        return "#b00000";
      case "NORMAL_TX":
        return (index + phase) % 4 === 0 ? "#5b00c8" : "#003cc8";
      default:
        return "#1a1a1a";
    }
  };

  return (
    <svg viewBox="0 0 200 200" className="w-full max-w-[220px] mx-auto" role="img">
      <circle cx="100" cy="100" r="86" fill="none" stroke="#A1A6D4" strokeWidth="1" />
      {Array.from({ length: count }, (_, i) => {
        const angle = (i / count) * 2 * Math.PI - Math.PI / 2;
        const cx = 100 + 72 * Math.cos(angle);
        const cy = 100 + 72 * Math.sin(angle);
        return <circle key={i} cx={cx} cy={cy} r="9" fill={colorFor(i)} />;
      })}
    </svg>
  );
}

export default function App() {
  const [probeStatus, setProbeStatus] = useState(null);
  const [systemState, setSystemState] = useState({ seq: 0, state: "IDLE", detail: {} });
  const [ml, setMl] = useState(null);
  const [connected, setConnected] = useState(false);
  const lastSeq = useRef(-1);

  // 1. État du système : SSE depuis remediation-engine (une seule vérité,
  //    partagée avec le terminal physique).
  useEffect(() => {
    const source = new EventSource(`${REMEDIATION_BASE}/events`);

    source.onopen = () => setConnected(true);
    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.seq <= lastSeq.current) return;
        lastSeq.current = data.seq;
        setSystemState(data);
        setConnected(true);
      } catch {
        // trame illisible : on ignore, le flux suivant corrigera
      }
    };
    source.onerror = () => setConnected(false);

    return () => source.close();
  }, []);

  // 2. Sonde de transaction légitime (preuve de vie continue).
  useEffect(() => {
    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/tx/auto-probe/status`);
        setProbeStatus(await res.json());
      } catch {
        setProbeStatus({ ok: false, unreachable: true });
      }
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => clearInterval(id);
  }, []);

  // 3. Score ML en direct (courbe du quadrant « Radar »).
  useEffect(() => {
    const poll = async () => {
      try {
        const res = await fetch(`${ML_BASE}/score`);
        setMl(await res.json());
      } catch {
        setMl(null);
      }
    };
    poll();
    const id = setInterval(poll, 1000);
    return () => clearInterval(id);
  }, []);

  const style = STATE_STYLE[systemState.state] || STATE_STYLE.IDLE;
  const detail = systemState.detail || {};

  return (
    <div className="min-h-screen bg-page p-6">
      <header className="flex items-center justify-between mb-6 gap-4">
        <div>
          <h1 className="text-ink text-2xl font-bold">RESILIENCE-ALPHA — SHADOW-SETTLE</h1>
          <p className="text-ink/70 text-sm">
            {style.text} · flux {connected ? "connecté" : "interrompu"} · seq {systemState.seq}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className={`text-xs font-bold ${connected ? "text-emerald" : "text-crimson"}`}>
            {connected ? "SSE OK" : "SSE KO"}
          </span>
          <StatusBadge state={systemState.state} />
        </div>
      </header>

      {/* Les 4 quadrants exigés pour le Clip Chrono, lisibles à distance */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <Card title="Flux transactionnel">
          <p className="text-ink text-sm italic">
            Dernière sonde :{" "}
            {probeStatus?.last_run
              ? new Date(probeStatus.last_run * 1000).toLocaleTimeString()
              : "…"}
          </p>
          <p className={`mt-2 text-lg font-bold ${probeStatus?.ok ? "text-emerald" : "text-crimson"}`}>
            {probeStatus?.unreachable
              ? "API INJOIGNABLE"
              : probeStatus?.ok
                ? "TX DE PREUVE : OK"
                : "TX DE PREUVE : ECHEC"}
          </p>
          {detail.key_image && (
            <p className="mt-1 text-ink/60 text-xs font-mono break-all">
              key image {detail.key_image.slice(0, 24)}…
            </p>
          )}
        </Card>

        <Card title="Radar machine learning">
          {ml ? (
            <>
              <div className="flex items-baseline gap-3">
                <p
                  className="text-4xl font-bold tabular-nums"
                  style={{ color: ml.is_anomaly ? "#c8102e" : "#0f9d63" }}
                >
                  {typeof ml.score === "number" ? ml.score.toFixed(2) : "—"}
                </p>
                <p className="text-ink/70 text-sm">
                  seuil {typeof ml.threshold === "number" ? ml.threshold.toFixed(2) : "—"}
                </p>
              </div>
              <p className="text-ink/70 text-xs mt-1">
                {ml.model === "ready"
                  ? `fenêtre ${ml.streak ?? 0}/${ml.streak_required ?? 3} · score appris, sans seuil fixe`
                  : `modèle : ${ml.model}`}
              </p>
              {ml.features && (
                <ul className="text-ink/60 text-[11px] font-mono mt-2 grid grid-cols-2 gap-x-3">
                  {Object.entries(ml.features).map(([name, value]) => (
                    <li key={name} className="truncate">
                      {name.replace(/_/g, " ")} : {typeof value === "number" ? value.toFixed(2) : value}
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <p className="text-crimson text-sm font-bold">DETECTEUR INJOIGNABLE</p>
          )}
        </Card>

        <Card title="Anneau & état du terminal">
          <LedRing state={systemState.state} seq={systemState.seq} />
          <p className="text-center text-ink/60 text-xs mt-2">
            miroir de l'objet physique · même état, même séquence
          </p>
        </Card>

        <Card title="Console de remédiation & conformité DORA">
          <p className="text-ink text-sm">
            {systemState.state === "REMEDIATING"
              ? "Playbook Ansible en cours…"
              : systemState.state === "RECOVERED_TX"
                ? `Reprise en ${detail.duration_seconds?.toFixed(1) ?? "?"} s`
                : "Aucune remédiation en cours"}
          </p>
          {detail.ansible_rc !== undefined && (
            <p className="text-ink/60 text-xs font-mono mt-1">ansible rc={detail.ansible_rc}</p>
          )}
        </Card>
      </div>
    </div>
  );
}
