import { useEffect, useRef, useState } from "react";

import PinPad from "./PinPad.jsx";
import RagAssistant from "./RagAssistant.jsx";
import AdminPanel from "./auth/AdminPanel.jsx";
import LoginPage from "./auth/LoginPage.jsx";
import { api } from "./auth/api.js";
import { clearSession, readToken } from "./auth/session.js";
import AdminShop from "./shop/AdminShop.jsx";
import Storefront from "./shop/Storefront.jsx";

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

function Card({ title, kicker, badge, legend, children, className = "" }) {
  return (
    <div className={`bg-card rounded-xl p-5 shadow-sm border border-rule/50 flex flex-col justify-between ${className}`}>
      <div>
        <div className="flex items-start justify-between gap-2 mb-1.5">
          <div>
            {kicker && (
              <p className="text-[10px] font-bold uppercase tracking-wider text-ink/70 mb-0.5">
                {kicker}
              </p>
            )}
            <h2 className="text-ink font-bold text-base md:text-lg leading-tight">{title}</h2>
          </div>
          {badge}
        </div>
        {legend && (
          <p className="text-ink/80 text-xs mb-3.5 leading-relaxed bg-white/40 rounded-lg p-2.5 border border-rule/30">
            {legend}
          </p>
        )}
        {children}
      </div>
    </div>
  );
}

const ANSIBLE_MAX_LINES = 120;

/** Heure courte pour le journal de la console. */
function clockLabel(ms) {
  if (!ms) return "--:--:--";
  return new Date(ms).toLocaleTimeString();
}

/**
 * Sortie Ansible → lignes affichables.
 *
 * `subprocess.run(capture_output=True)` ne produit pas de codes couleur, mais
 * on les retire quand même : une sortie rejouée depuis un terminal coloré ne
 * doit pas polluer la console. Les lignes vides finales sont coupées.
 */
function splitAnsibleOutput(text) {
  const lines = String(text ?? "")
    .replace(/\u001b\[[0-9;]*m/g, "")
    .replace(/\r/g, "")
    .split("\n");
  while (lines.length > 0 && lines[lines.length - 1].trim() === "") lines.pop();
  return lines;
}

/** Coloration minimale, calquée sur la sortie d'`ansible-playbook`. */
function ansibleLineClass(line) {
  if (/^TASK\b/.test(line)) return "text-amber-300 font-bold";
  if (/^PLAY\b/.test(line)) return "text-emerald font-bold";
  if (/^(fatal|failed|ERROR)\b/.test(line)) return "text-red-400 font-bold";
  if (/^(changed|included)\b/.test(line)) return "text-emerald";
  if (/^(ok|skipping)\b/.test(line)) return "text-page/70";
  return "text-page/80";
}

/**
 * Console du playbook Ansible (quadrant « remédiation »).
 *
 * Pendant `REMEDIATING`, `/history` est interrogé toutes les secondes ; le
 * polling s'arrête dès que l'état quitte `REMEDIATING`, avec une dernière
 * lecture à cet instant-là. L'extrait (`ansible_stdout_tail`) reste ensuite
 * affiché avec `ansible_rc`.
 *
 * Deux limites sont assumées et visibles à l'écran :
 *
 * * `remediation-engine` n'ajoute l'entrée à `/history` qu'APRÈS le playbook
 *   (`remediation_log.append` suit `_run_playbook`) : stdout n'est pas diffusé
 *   tâche par tâche. En attendant l'extrait, la console affiche donc le journal
 *   réel de l'incident (alertes corrélées, diagnostic, chrono), jamais du
 *   contenu inventé ;
 * * `/history` est une route publique (aucune dépendance `SERVICE_TOKEN`) :
 *   aucun jeton de service n'est nécessaire — donc aucun secret dans le bundle.
 *
 * Si le moteur est injoignable, on l'écrit : pas d'écran vide, pas d'exception.
 */
function AnsibleConsole({ state, detail }) {
  const [history, setHistory] = useState(null);
  const [unreachable, setUnreachable] = useState(false);
  const [startedAt, setStartedAt] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  const boxRef = useRef(null);

  const running = state === "REMEDIATING";

  // Chrono : la seule animation pendant l'exécution, et elle est réelle.
  useEffect(() => {
    if (!running) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [running]);

  // Début de l'exécution observée : distingue l'extrait de CET incident de
  // celui de l'incident précédent.
  useEffect(() => {
    setStartedAt(state === "REMEDIATING" ? Date.now() : null);
    setNow(Date.now());
  }, [state]);

  // /history : polling 1 s pendant REMEDIATING, dernière lecture dès que l'état
  // quitte REMEDIATING (c'est précisément là que l'entrée est écrite).
  useEffect(() => {
    if (
      state !== "REMEDIATING" &&
      state !== "RECOVERED_TX" &&
      state !== "REMEDIATION_FAILED"
    ) {
      return undefined;
    }
    let cancelled = false;

    const read = async () => {
      try {
        const response = await fetch(`${REMEDIATION_BASE}/history`, { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        if (cancelled) return;
        setHistory(payload);
        setUnreachable(false);
      } catch {
        // moteur coupé ou route absente : on le dit, on ne casse pas l'écran
        if (!cancelled) setUnreachable(true);
      }
    };

    read();
    if (state !== "REMEDIATING") return () => { cancelled = true; };
    const id = window.setInterval(read, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [state]);

  const entries = Array.isArray(history?.entries) ? history.entries : [];
  const latest = entries.length > 0 ? entries[entries.length - 1] : null;
  const startedAtSeconds = startedAt ? startedAt / 1000 : null;
  // L'entrée la plus récente appartient à cet incident si elle porte son
  // horodatage — ou si l'on est déjà sorti de REMEDIATING, puisque le moteur
  // l'écrit juste avant de publier l'état final.
  const latestIsCurrent =
    latest !== null &&
    (state !== "REMEDIATING" ||
      typeof latest.detected_at !== "number" ||
      (startedAtSeconds !== null && latest.detected_at >= startedAtSeconds - 5));

  const tail = latest
    ? splitAnsibleOutput(latest.ansible_stdout_tail).slice(-ANSIBLE_MAX_LINES)
    : [];

  const elapsedSeconds = startedAt ? (now - startedAt) / 1000 : 0;
  const rows = [];

  // Si le moteur publiait un jour `ansible_stdout_tail` dans le détail SSE de
  // REMEDIATING (aujourd'hui il ne le fait qu'à la fin, via /history), on
  // l'afficherait tel quel : ce composant n'aurait pas à changer.
  const liveTail = running
    ? splitAnsibleOutput(detail?.ansible_stdout_tail).slice(-ANSIBLE_MAX_LINES)
    : [];
  const showJournal = running && liveTail.length === 0 && !latestIsCurrent;

  if (liveTail.length > 0) {
    liveTail.forEach((line, index) =>
      rows.push({ key: `direct-${index}`, className: ansibleLineClass(line), text: line })
    );
  } else if (showJournal) {
    const alerts = Array.isArray(detail?.alerts) ? detail.alerts : [];
    alerts.forEach((alert, index) => {
      const severity = alert.severity ? ` (${alert.severity})` : "";
      rows.push({
        key: `alerte-${index}`,
        className: "text-page/70",
        text: `[${clockLabel((alert.received_at ?? 0) * 1000)}] alerte ${index + 1}/${
          alerts.length
        } : ${alert.alertname ?? "?"}${severity}`,
      });
    });
    if (detail?.diagnostic) {
      rows.push({
        key: "diagnostic",
        className: "text-page/70",
        text: `[${clockLabel(startedAt)}] diagnostic : ${detail.diagnostic}`,
      });
    }
    rows.push({
      key: "playbook",
      className: "text-page/70",
      text: `[${clockLabel(startedAt)}] ansible-playbook remediation_playbook.yml`,
    });
    rows.push({
      key: "elapsed",
      className: "text-amber-300",
      text: `[${clockLabel(now)}] exécution en cours… ${elapsedSeconds.toFixed(1)} s`,
    });
    if (unreachable) {
      rows.push({
        key: "indisponible",
        className: "text-red-400 font-bold",
        text: "sortie du playbook indisponible — remediation-engine /history injoignable",
      });
    }
    if (tail.length > 0) {
      rows.push({
        key: "separateur",
        className: "text-page/40",
        text: "— extrait de l'incident précédent —",
      });
      tail.forEach((line, index) =>
        rows.push({ key: `precedent-${index}`, className: ansibleLineClass(line), text: line })
      );
    }
  } else if (tail.length > 0) {
    tail.forEach((line, index) =>
      rows.push({ key: `sortie-${index}`, className: ansibleLineClass(line), text: line })
    );
  }

  const emptyMessage = unreachable
    ? "sortie du playbook indisponible — remediation-engine /history injoignable"
    : running
      ? "sortie du playbook indisponible pour l'instant — le moteur la publie à la fin de l'exécution"
      : "aucun playbook exécuté pour l'instant";

  let statusLabel = "en attente";
  let statusClass = "text-ink/60";
  if (unreachable) {
    statusLabel = "indisponible";
    statusClass = "text-crimson";
  } else if (running) {
    statusLabel = `en cours · ${elapsedSeconds.toFixed(1)} s`;
    statusClass = "text-ink";
  } else if (latestIsCurrent && latest?.ansible_rc !== undefined) {
    statusLabel = `terminé · rc=${latest.ansible_rc}`;
    statusClass = latest.ansible_rc === 0 ? "text-emerald" : "text-crimson";
  }

  // Défilement automatique vers le bas à chaque nouvelle ligne.
  const signature = rows.map((row) => row.text).join("\n");
  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [signature]);

  return (
    <div className="mt-3">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-ink/70 text-xs font-bold uppercase tracking-wide">
          Sortie du playbook
        </p>
        <p className={`text-[11px] font-mono font-bold ${statusClass}`}>{statusLabel}</p>
      </div>
      <div
        ref={boxRef}
        role="log"
        className="mt-1 h-40 overflow-y-auto rounded-lg bg-ink px-3 py-2 font-mono text-[11px] leading-relaxed"
      >
        {rows.length === 0 ? (
          <p className="text-page/50">{emptyMessage}</p>
        ) : (
          rows.map((row) => (
            <p key={row.key} className={`whitespace-pre-wrap break-all ${row.className}`}>
              {row.text || " "}
            </p>
          ))
        )}
      </div>
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

  const isCurrent = (st) => state === st;

  return (
    <div>
      <svg viewBox="0 0 200 200" className="w-full max-w-[190px] mx-auto drop-shadow-sm" role="img">
        <circle cx="100" cy="100" r="86" fill="none" stroke="#8A8FC0" strokeWidth="1" strokeDasharray="3 3" />
        {Array.from({ length: count }, (_, i) => {
          const angle = (i / count) * 2 * Math.PI - Math.PI / 2;
          const cx = 100 + 72 * Math.cos(angle);
          const cy = 100 + 72 * Math.sin(angle);
          return <circle key={i} cx={cx} cy={cy} r="9" fill={colorFor(i)} />;
        })}
      </svg>

      {/* Légende complète et interactive des 4 états de l'anneau */}
      <div className="mt-3 pt-3 border-t border-rule/30">
        <p className="text-[10px] font-bold uppercase tracking-wider text-ink/70 mb-2 text-center">
          Légende des états lumineux (Miroir ESP32 / LED)
        </p>
        <div className="grid grid-cols-2 gap-1.5 text-[11px]">
          <div
            className={`p-1.5 rounded-lg flex items-center gap-2 border transition ${
              isCurrent("NORMAL_TX")
                ? "bg-white border-ink shadow-sm font-bold text-ink"
                : "bg-white/30 border-transparent text-ink/75"
            }`}
          >
            <span className="w-3 h-3 rounded-full bg-[#003cc8] shrink-0 shadow-sm" />
            <span className="truncate"><strong>Bleu</strong> : Nominal</span>
          </div>
          <div
            className={`p-1.5 rounded-lg flex items-center gap-2 border transition ${
              isCurrent("UNDER_ATTACK")
                ? "bg-white border-crimson shadow-sm font-bold text-crimson"
                : "bg-white/30 border-transparent text-ink/75"
            }`}
          >
            <span className="w-3 h-3 rounded-full bg-[#dc0000] shrink-0 shadow-sm animate-pulse" />
            <span className="truncate"><strong>Rouge</strong> : Attaque</span>
          </div>
          <div
            className={`p-1.5 rounded-lg flex items-center gap-2 border transition ${
              isCurrent("REMEDIATING")
                ? "bg-white border-amber-600 shadow-sm font-bold text-amber-700"
                : "bg-white/30 border-transparent text-ink/75"
            }`}
          >
            <span className="w-3 h-3 rounded-full bg-[#ff8c00] shrink-0 shadow-sm" />
            <span className="truncate"><strong>Orange</strong> : Remédiation</span>
          </div>
          <div
            className={`p-1.5 rounded-lg flex items-center gap-2 border transition ${
              isCurrent("RECOVERED_TX")
                ? "bg-white border-emerald shadow-sm font-bold text-emerald"
                : "bg-white/30 border-transparent text-ink/75"
            }`}
          >
            <span className="w-3 h-3 rounded-full bg-[#0f9d63] shrink-0 shadow-sm" />
            <span className="truncate"><strong>Vert</strong> : Rétabli</span>
          </div>
        </div>
      </div>
    </div>
  );
}

const FEATURE_META = {
  decoy_median_age_days: { label: "Âge médian leurres", unit: "j", highlight: true },
  ring_age_skewness: { label: "Asymétrie anneau (skewness)", unit: "" },
  ring_verify_cpu_us: { label: "CPU vérif. anneau", unit: "µs" },
  inter_arrival_median: { label: "Médiane intervalle inter-tx", unit: "s" },
  tx_latency_p95: { label: "Latence p95", unit: "ms" },
  key_image_rejection_ratio: { label: "Taux rejets PIN / rejeu", unit: "%" },
};

function Supervision({ user, onLogout, onOpenAdmin, onOpenShopAdmin, onOpenStorefront }) {
  const [probeStatus, setProbeStatus] = useState(null);
  const [systemState, setSystemState] = useState({ seq: 0, state: "IDLE", detail: {} });
  const [ml, setMl] = useState(null);
  // Audit view-only : montant déchiffré avec la SEULE View Key (DORA art. 19).
  const [audit, setAudit] = useState(null);
  // Compteurs PIN (surface d'attaque physique, alimente le ML).
  const [pinStats, setPinStats] = useState(null);
  const [connected, setConnected] = useState(false);
  const lastSeq = useRef(-1);

  // 1. État du système : SSE depuis remediation-engine (une seule vérité,
  //    partagée avec le terminal physique).
  useEffect(() => {
    const source = new EventSource(`${REMEDIATION_BASE}/events`);

    const applyEvent = (data) => {
      if (typeof data?.seq !== "number" || data.seq <= lastSeq.current) return;
      lastSeq.current = data.seq;
      setSystemState(data);
    };

    const resync = async () => {
      try {
        const response = await fetch(`${REMEDIATION_BASE}/state`);
        if (response.ok) applyEvent(await response.json());
      } catch {
        // moteur injoignable : le flux SSE reprendra quand il reviendra
      }
    };

    source.onopen = () => {
      setConnected(true);
      resync();
    };
    source.onmessage = (event) => {
      try {
        applyEvent(JSON.parse(event.data));
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

  // 4. Auditabilité : le portefeuille expose le montant de la dernière
  //    transaction déchiffré sans jamais utiliser la Spend Key.
  useEffect(() => {
    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/audit/last`);
        setAudit(await res.json());
      } catch {
        setAudit(null);
      }
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => clearInterval(id);
  }, []);

  // 5. Statistiques du pavé : le taux de refus est une feature du détecteur.
  useEffect(() => {
    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/pin/stats`);
        setPinStats(await res.json());
      } catch {
        setPinStats(null);
      }
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => clearInterval(id);
  }, []);

  const style = STATE_STYLE[systemState.state] || STATE_STYLE.IDLE;
  const detail = systemState.detail || {};

  return (
    <div className="min-h-screen bg-page p-4 md:p-6 pb-20 font-montserrat">
      {/* En-tête officiel du Back-office avec navigation et statut en direct */}
      <header className="bg-card/70 border border-rule/50 rounded-2xl p-4 md:p-5 mb-6 shadow-sm">
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-bold uppercase tracking-wider bg-ink text-white px-2 py-0.5 rounded">
                Back-office sécurisé 2FA
              </span>
              <span className="text-[10px] font-bold text-ink/70">
                seq #{systemState.seq} · flux {connected ? "SSE connecté" : "reconnexion…"}
              </span>
            </div>
            <h1 className="text-ink text-xl md:text-2xl font-black tracking-tight">
              RESILIENCE-ALPHA — SUPERVISION & WAR ROOM
            </h1>
            <p className="text-ink/75 text-xs mt-0.5">
              {style.text} · Détection d'anomalies ML, remédiation autonome SOAR et conformité DORA.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            {/* Grand badge d'état lisible à distance pour la soutenance */}
            <div className="scale-105">
              <StatusBadge state={systemState.state} />
            </div>

            {/* Profil connecté */}
            {user && (
              <span className="bg-white/80 border border-rule/30 text-ink text-xs font-bold px-3 py-1.5 rounded-lg shadow-sm">
                👤 {user.username} <span className="opacity-60 text-[10px]">({user.role})</span>
              </span>
            )}

            {/* Onglets rapides de navigation */}
            {onOpenAdmin && (
              <button
                onClick={onOpenAdmin}
                className="bg-white hover:bg-white/90 text-ink text-xs font-bold rounded-lg px-3 py-1.5 border border-rule shadow-sm transition active:scale-95"
              >
                Habilitations (2FA)
              </button>
            )}
            {onOpenShopAdmin && (
              <button
                onClick={onOpenShopAdmin}
                className="bg-white hover:bg-white/90 text-ink text-xs font-bold rounded-lg px-3 py-1.5 border border-rule shadow-sm transition active:scale-95"
              >
                Admin Boutique
              </button>
            )}
            {onOpenStorefront && (
              <button
                onClick={onOpenStorefront}
                className="bg-white hover:bg-white/90 text-ink text-xs font-bold rounded-lg px-3 py-1.5 border border-rule shadow-sm transition active:scale-95"
              >
                Vitrine publique
              </button>
            )}
            {onLogout && (
              <button
                onClick={onLogout}
                className="bg-ink hover:bg-ink/90 text-white text-xs font-bold rounded-lg px-3 py-1.5 shadow-sm transition active:scale-95"
              >
                Déconnexion
              </button>
            )}
          </div>
        </div>
      </header>

      {/* Grille principale des 4 quadrants P0 + Console DORA + Assistant RAG */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        
        {/* QUADRANT 1 : FLUX TRANSACTIONNEL & SONDE DE VIVACITÉ */}
        <Card
          kicker="PILIER CRYPTO · PREUVE DE VIE"
          title="Flux transactionnel & Vivacité"
          badge={
            <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${probeStatus?.ok ? "bg-emerald/20 text-emerald border border-emerald/40" : "bg-crimson/20 text-crimson border border-crimson/40"}`}>
              {probeStatus?.ok ? "SONDE ACTIVE (4s)" : "SONDE EN PANNE"}
            </span>
          }
          legend="Un robot en arrière-plan émet et dépense une transaction légitime toutes les 4s pour garantir en direct la vivacité du noyau Rust et alimenter le ML en données de référence."
        >
          <div className="space-y-3 mt-1">
            <div className="p-3 bg-white/50 rounded-xl border border-rule/30 flex items-center justify-between">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-wider text-ink/60">Dernière transaction de sonde</p>
                <p className="text-sm font-bold text-ink">
                  {probeStatus?.last_run
                    ? new Date(probeStatus.last_run * 1000).toLocaleTimeString()
                    : "En attente du premier cycle…"}
                </p>
              </div>
              <div className="text-right">
                <span className={`text-xs font-black px-2.5 py-1 rounded-lg ${probeStatus?.ok ? "bg-emerald text-white" : "bg-crimson text-white"}`}>
                  {probeStatus?.unreachable ? "API INJOIGNABLE" : probeStatus?.ok ? "TX DE PREUVE : OK" : "TX DE PREUVE : ECHEC"}
                </span>
              </div>
            </div>

            {detail.key_image ? (
              <div className="p-3 bg-white/40 rounded-xl border border-rule/30">
                <p className="text-[10px] font-bold uppercase tracking-wider text-ink/65 mb-1">
                  Key Image (Empreinte anti-double-dépense)
                </p>
                <p className="font-mono text-xs text-ink break-all bg-white/70 p-2 rounded-lg border border-rule/20 select-all">
                  {detail.key_image}
                </p>
              </div>
            ) : (
              <div className="p-3 bg-white/30 rounded-xl border border-rule/20 text-xs text-ink/60 italic text-center">
                Key image enregistrée dans le registre d'outputs
              </div>
            )}
          </div>
        </Card>

        {/* QUADRANT 2 : RADAR MACHINE LEARNING */}
        <Card
          kicker="PILIER IA · DÉTECTION D'ANOMALIES"
          title="Radar Machine Learning"
          badge={
            <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${ml?.is_anomaly ? "bg-crimson text-white animate-pulse" : "bg-white text-ink border border-rule/30"}`}>
              {ml?.is_anomaly ? "ANOMALIE CONFIRMÉE" : "MODÈLE PRÊT"}
            </span>
          }
          legend="Surveillance continue sur 6 features physiques sans seuil codé en dur. Un score anormal pendant 3 fenêtres consécutives déclenche automatiquement l'incident."
        >
          {ml ? (
            <div className="space-y-3 mt-1">
              <div className="p-3 bg-white/50 rounded-xl border border-rule/30">
                <div className="flex items-baseline justify-between mb-1">
                  <div>
                    <span className="text-3xl font-black tabular-nums tracking-tight" style={{ color: ml.is_anomaly ? "#c8102e" : "#0f9d63" }}>
                      {typeof ml.score === "number" ? ml.score.toFixed(2) : "—"}
                    </span>
                    <span className="text-ink/60 text-xs ml-2 font-semibold">
                      / seuil appris {typeof ml.threshold === "number" ? ml.threshold.toFixed(2) : "9.50"}
                    </span>
                  </div>
                  <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${ml.is_anomaly ? "bg-crimson/15 text-crimson" : "bg-emerald/15 text-emerald"}`}>
                    {ml.is_anomaly ? "DÉPASSEMENT" : "NOMINAL"}
                  </span>
                </div>

                {/* Barre de jauge du score ML */}
                <div className="w-full bg-white/80 rounded-full h-2.5 overflow-hidden border border-rule/30 my-2">
                  <div
                    className={`h-full transition-all duration-500 rounded-full ${ml.is_anomaly ? "bg-crimson" : "bg-emerald"}`}
                    style={{
                      width: `${Math.min(100, Math.max(5, ((ml.score ?? 0) / (ml.threshold ?? 9.5)) * 100))}%`
                    }}
                  />
                </div>

                <div className="flex items-center justify-between text-[11px] text-ink/75 pt-1">
                  <span>Anti-rebond (streak) :</span>
                  <strong className="font-mono bg-white/80 px-2 py-0.5 rounded border border-rule/20">
                    {ml.streak ?? 0} / {ml.streak_required ?? 3} fenêtres consécutives
                  </strong>
                </div>
              </div>

              {/* Grille des 6 features physiques */}
              {ml.features && (
                <div className="grid grid-cols-2 gap-2 text-[11px]">
                  {Object.entries(ml.features).map(([key, val]) => {
                    const meta = FEATURE_META[key] || { label: key.replace(/_/g, " "), unit: "" };
                    const formatted = typeof val === "number" ? val.toFixed(2) : val;
                    return (
                      <div
                        key={key}
                        className={`p-2 rounded-lg border flex flex-col justify-between ${
                          meta.highlight
                            ? "bg-white/80 border-ink/40 shadow-xs"
                            : "bg-white/40 border-rule/25"
                        }`}
                      >
                        <span className="text-[10px] text-ink/70 font-semibold truncate leading-tight">
                          {meta.label}
                        </span>
                        <span className="text-xs font-mono font-bold text-ink mt-0.5">
                          {formatted} {meta.unit}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ) : (
            <div className="p-4 bg-crimson/10 border border-crimson/30 rounded-xl text-crimson font-bold text-xs text-center">
              DÉTECTEUR MACHINE LEARNING INJOIGNABLE (port 8083)
            </div>
          )}
        </Card>

        {/* QUADRANT 3 : ANNEAU & ÉTAT DU TERMINAL MATÉRIEL */}
        <Card
          kicker="PILIER MATÉRIEL · ESP32 / LED"
          title="Anneau & État du terminal"
          badge={
            <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-white text-ink border border-rule/30">
              Miroir physique 24 LEDs
            </span>
          }
          legend="Miroir exact de l'anneau de LEDs présent sur le boîtier physique (ESP32 et bandeau bicolore ESP8266). Si le boîtier est débranché, l'animation garantit une soutenance 100% démontrable."
        >
          <LedRing state={systemState.state} seq={systemState.seq} />
        </Card>

        {/* QUADRANT 4 : TERMINAL PHYSIQUE (SAISIE DU CODE PIN) */}
        <Card
          kicker="PILIER CYBER · SURFACE D'ATTAQUE PHYSIQUE"
          title="Terminal physique — Saisie du PIN"
          badge={
            <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-white text-ink border border-rule/30">
              PIN Démo : 5771
            </span>
          }
          legend="Simulateur du clavier du terminal physique de caisse. Un code PIN valide déclenche une transaction réelle bLSAG. Des refus répétés simulent une attaque par force brute et alertent le ML."
        >
          <PinPad apiBase={API_BASE} />

          {pinStats && (
            <div className="mt-3 p-2 bg-white/40 rounded-lg border border-rule/25 text-center text-[11px] text-ink/75">
              <span>Refus enregistrés : </span>
              <strong className="font-mono text-ink">
                {pinStats.failures} / {pinStats.attempts} sur {pinStats.window_seconds}s
              </strong>
              {pinStats.failures > 0 && (
                <span className="text-crimson font-bold ml-2">
                  (taux {(pinStats.failure_ratio * 100).toFixed(0)}%)
                </span>
              )}
            </div>
          )}
        </Card>

        {/* QUADRANT 5 : CONSOLE DE REMÉDIATION AUTONOME SOAR & AUDIT DORA (ART. 19) */}
        <Card
          kicker="PILIER INFRA & GOUVERNANCE SOAR"
          title="Console de remédiation SOAR & Audit DORA (art. 19)"
          badge={
            <span className={`text-xs font-bold px-3 py-1 rounded-full ${
              systemState.state === "REMEDIATING"
                ? "bg-amber-500 text-white animate-pulse"
                : systemState.state === "RECOVERED_TX"
                  ? "bg-emerald text-white"
                  : "bg-white text-ink border border-rule/40"
            }`}>
              {systemState.state === "REMEDIATING"
                ? "REMEDIATION EN COURS"
                : systemState.state === "RECOVERED_TX"
                  ? "REPRISE VALIDEE"
                  : "EN ATTENTE D'INCIDENT"}
            </span>
          }
          legend="Réponse à incident 100% autonome sans humain : le moteur lance un playbook Ansible qui reconfigure le WAF et déroute l'attaquant vers le honeypot. La View Key permet à l'auditeur externe de déchiffrer les transactions sans risque de vol."
          className="md:col-span-2"
        >
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 mt-2">
            
            {/* Colonne gauche : Playbook Ansible */}
            <div className="lg:col-span-7 flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between mb-1">
                  <p className="text-ink font-bold text-sm">Sortie du Playbook Ansible</p>
                  {detail.duration_seconds !== undefined && (
                    <span className="text-emerald font-bold text-xs bg-emerald/15 px-2 py-0.5 rounded border border-emerald/30">
                      Durée : {detail.duration_seconds.toFixed(1)} s
                    </span>
                  )}
                </div>
                <p className="text-ink/75 text-xs mb-2">
                  {systemState.state === "REMEDIATING"
                    ? "Exécution asynchrone du playbook de remédiation sans intervention humaine…"
                    : systemState.state === "RECOVERED_TX"
                      ? `Reprise en ${detail.duration_seconds?.toFixed(1) ?? "?"} s (marge < 90s respectée)`
                      : "Prêt : le playbook se déclenche automatiquement à la première alerte."}
                </p>
              </div>
              <AnsibleConsole state={systemState.state} detail={detail} />
            </div>

            {/* Colonne droite : Audit View-Only DORA */}
            <div className="lg:col-span-5 bg-white/70 rounded-xl p-4 border border-rule/40 flex flex-col justify-between shadow-xs">
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-ink/70">
                    DORA ART. 19 & NIS2
                  </span>
                  <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-ink/10 text-ink">
                    Audit View-Only
                  </span>
                </div>
                <h3 className="text-ink font-bold text-sm mb-1.5">
                  Contrôle réglementaire sans Spend Key
                </h3>
                <p className="text-ink/75 text-[11px] leading-relaxed mb-3">
                  L'auditeur bancaire détient <strong>uniquement la View Key</strong> : il déchiffre les montants réels en base mais ne possède <strong>aucun pouvoir de signature</strong> (aucun risque de vol de fonds).
                </p>

                {audit?.available ? (
                  <div className="bg-page/70 rounded-lg p-3 border border-rule/30 space-y-2">
                    <div>
                      <span className="text-[10px] uppercase font-bold text-ink/60">Dernier montant déchiffré :</span>
                      <p className="text-2xl font-black text-ink tabular-nums">
                        {audit.amount} centimes <span className="text-sm font-semibold text-ink/70">({(audit.amount / 100).toFixed(2)} €)</span>
                      </p>
                    </div>
                    <div className="text-[11px] font-mono text-ink/80 space-y-1 pt-1.5 border-t border-rule/20">
                      <p className="truncate"><strong>Output :</strong> {audit.output_id}</p>
                      <p><strong>Anneau bLSAG :</strong> {audit.ring_size} clés (1 réelle + 10 leurres)</p>
                      <p className="truncate"><strong>Key Image :</strong> {String(audit.key_image).slice(0, 16)}…</p>
                    </div>
                  </div>
                ) : (
                  <div className="bg-page/50 rounded-lg p-3 text-center text-xs text-ink/60 border border-rule/30">
                    {audit?.reason ?? "En attente d'une transaction confirmée…"}
                  </div>
                )}
              </div>

              <p className="text-ink/50 text-[10px] mt-3 italic text-center">
                ⚡ Données actualisées en direct par la sonde de vivacité (toutes les 4s)
              </p>
            </div>
          </div>
        </Card>

        {/* QUADRANT 6 : ASSISTANT RAG TECHNIQUE LOCAL */}
        <Card
          kicker="PILIER IA · DOCUMENTATION TECHNIQUE"
          title="Assistant RAG — dossier technique"
          badge={
            <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-white text-ink border border-rule/30">
              BM25 + Ollama local
            </span>
          }
          legend="Interroge l'ensemble du dossier technique, des rapports EBIOS RM et des règles du jury en langage naturel, avec citation rigoureuse des sources et mode dégradé automatique si Ollama est éteint."
          className="md:col-span-2"
        >
          <div id="assistant-rag" className="mt-1">
            <RagAssistant />
          </div>
        </Card>

      </div>
    </div>
  );
}

/**
 * Racine de l'application — la vitrine est publique, le back-office ne l'est pas.
 *
 * Deux consignes cohabitent :
 *
 * * la **boutique** est un site vitrine : un visiteur consulte le catalogue,
 *   remplit un panier et paie sans compte. C'est l'écran par défaut.
 * * le **back-office** (supervision, habilitations, administration de la
 *   boutique) exige l'authentification à deux facteurs : identifiant, visage,
 *   mot de passe.
 *
 * Les appels de supervision (8080, 8083, 8084) ne partent qu'une fois la
 * session ouverte. Ce n'est pas une frontière de sécurité — les services de
 * démonstration restent joignables — mais cela évite qu'un écran de supervision
 * s'affiche devant quelqu'un qui n'a pas franchi la reconnaissance faciale.
 */

/**
 * Barre de navigation de démonstration.
 *
 * Pourquoi elle existe : en soutenance, on n'a pas le temps de chercher un
 * bouton. Les quatre écrans utiles sont à un clic, numérotés dans l'ordre du
 * storyboard, et l'étape courante est visible. Sans elle, « Habilitations →
 * Boutique » se perd au milieu d'un panneau d'administration.
 *
 * Elle n'ajoute aucune autorisation : les étapes 2 à 4 restent fermées sans
 * session, et le serveur refuse de toute façon (401/403).
 */
const DEMO_STEPS = [
  { view: "shop", label: "1 · Vitrine", hint: "catalogue, panier, ticket" },
  { view: "supervision", label: "2 · Supervision", hint: "badge, score ML, Ansible" },
  { view: "admin", label: "3 · Habilitations", hint: "visage, rôles, audit" },
  { view: "shop-admin", label: "4 · Admin boutique", hint: "articles, commandes" },
  { view: "rag", label: "5 · Assistant RAG", hint: "question, sources, moteur" },
];

function DemoNav({ view, user, onNavigate }) {
  return (
    <nav
      aria-label="Navigation de démonstration"
      className="fixed bottom-3 left-1/2 -translate-x-1/2 z-50 flex items-center gap-1 rounded-full bg-ink/95 px-2 py-1.5 shadow-xl"
    >
      <span className="px-2 text-[10px] font-bold uppercase tracking-wider text-page/50">
        démo
      </span>
      {DEMO_STEPS.map((step) => {
        const locked = step.view !== "shop" && !user;
        const active = view === step.view;
        return (
          <button
            key={step.view}
            onClick={() => onNavigate(locked ? "login" : step.view)}
            title={locked ? "session back-office requise" : step.hint}
            className={
              active
                ? "rounded-full bg-page px-3 py-1 text-[11px] font-bold text-ink"
                : "rounded-full px-3 py-1 text-[11px] font-bold text-page/80 hover:bg-page/10"
            }
          >
            {step.label}
            {locked ? " 🔒" : ""}
          </button>
        );
      })}
    </nav>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(null);
  const [view, setView] = useState("shop");
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const boot = async () => {
      try {
        const status = await api.health();
        if (!cancelled) {
          setHealth(status);
          setHealthError(null);
        }
      } catch (error) {
        if (!cancelled) setHealthError(error.message);
      }
      // Une session en storage n'est valide que si le serveur la reconnaît :
      // un redémarrage du back-office ou une révocation la rend invalide.
      if (readToken()) {
        try {
          const me = await api.me();
          if (!cancelled) setUser(me.user);
        } catch {
          clearSession();
        }
      }
      if (!cancelled) setChecking(false);
    };
    boot();
    const id = window.setInterval(async () => {
      try {
        setHealth(await api.health());
        setHealthError(null);
      } catch (error) {
        setHealthError(error.message);
      }
    }, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const logout = async () => {
    try {
      await api.logout();
    } catch {
      // déconnexion locale même si le serveur est muet
    }
    clearSession();
    setUser(null);
    setView("shop");
  };

  let content;

  if (checking) {
    content = (
      <div className="min-h-screen bg-page flex items-center justify-center">
        <p className="text-ink font-bold">Vérification de la session…</p>
      </div>
    );
  } else if (view === "login" || ((view === "supervision" || view === "admin" || view === "shop-admin") && !user)) {
    // Le back-office exige une session : sans elle, on retombe sur la connexion.
    content = (
      <div className="relative">
        {healthError && (
          <div className="absolute top-0 left-0 right-0 bg-crimson text-white text-center text-xs font-bold py-1">
            back-office injoignable ({healthError}) — démarrez le service sur le PC hôte
          </div>
        )}
        <LoginPage
          health={health}
          onAuthenticated={(authenticated) => {
            setUser(authenticated);
            setView("supervision");
          }}
        />
        <button
          onClick={() => setView("shop")}
          className="fixed bottom-4 left-4 bg-white text-ink text-xs font-bold rounded-lg px-3 py-2 border border-rule"
        >
          ← Retour à la boutique
        </button>
      </div>
    );
  } else if (view === "admin" && user) {
    content = (
      <AdminPanel
        currentUser={user}
        onLogout={logout}
        onOpenShop={() => setView("shop-admin")}
      />
    );
  } else if (view === "shop-admin" && user) {
    content = (
      <div className="relative">
        <AdminShop />
        <button
          onClick={() => setView("admin")}
          className="fixed bottom-4 left-4 bg-ink text-white text-xs font-bold rounded-lg px-3 py-2"
        >
          ← Habilitations
        </button>
      </div>
    );
  } else if (view === "supervision" && user) {
    content = (
      <Supervision
        user={user}
        onLogout={logout}
        onOpenAdmin={user.role === "admin" ? () => setView("admin") : undefined}
        onOpenShopAdmin={() => setView("shop-admin")}
        onOpenStorefront={() => setView("shop")}
      />
    );
  } else {
    content = (
      <Storefront
        user={user}
        onLogin={() => setView("login")}
        onSupervision={() => setView("supervision")}
        onAdmin={() => setView("admin")}
        onLogout={logout}
      />
    );
  }

  return (
    <>
      {content}
      <DemoNav
        view={view}
        user={user}
        onNavigate={(next) => {
          if (next === "rag") {
            // L'assistant est un quadrant de la supervision : on y va et on
            // fait défiler jusqu'à lui. Sans ce raccourci, il faut chercher
            // sous la console Ansible pendant la démonstration.
            setView("supervision");
            window.setTimeout(() => {
              document.getElementById("assistant-rag")?.scrollIntoView({
                behavior: "smooth",
                block: "center",
              });
            }, 200);
            return;
          }
          setView(next);
        }}
      />
    </>
  );
}
