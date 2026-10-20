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

function Card({ title, children, className = "" }) {
  return (
    <div className={`bg-card rounded-xl p-5 h-full ${className}`}>
      <h2 className="text-ink font-bold mb-3">{title}</h2>
      {children}
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

function Supervision({ user, onLogout, onOpenAdmin }) {
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
  //
  //    Au (re)connect, on relit `/state` : le flux SSE ne rejoue pas le dernier
  //    état. Sans cette relecture, un dashboard ouvert APRÈS un changement
  //    (cas de la page protégée par l'authentification) resterait sur « INIT »
  //    alors que le système est en NORMAL_TX ou en REMEDIATING. Le pont
  //    hardware fait exactement la même chose.
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
    <div className="min-h-screen bg-page p-6">
      <header className="flex items-center justify-between mb-6 gap-4">
        <div>
          <h1 className="text-ink text-2xl font-bold">RESILIENCE-ALPHA — SHADOW-SETTLE</h1>
          <p className="text-ink/70 text-sm">
            {style.text} · flux {connected ? "connecté" : "interrompu"} · seq {systemState.seq}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {user && (
            <span className="text-ink/70 text-xs">
              {user.username} · {user.role}
            </span>
          )}
          {onOpenAdmin && (
            <button
              onClick={onOpenAdmin}
              className="bg-white text-ink text-xs font-bold rounded-lg px-3 py-1.5 border border-rule"
            >
              Habilitations
            </button>
          )}
          {onLogout && (
            <button
              onClick={onLogout}
              className="bg-ink text-white text-xs font-bold rounded-lg px-3 py-1.5"
            >
              Déconnexion
            </button>
          )}
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

        <Card title="Terminal — saisie du code">
          <PinPad apiBase={API_BASE} />
          {pinStats && (
            <p className="mt-2 text-center text-ink/60 text-[11px]">
              {pinStats.failures}/{pinStats.attempts} refus sur {pinStats.window_seconds} s
              {pinStats.failures > 0 && (
                <span className="text-crimson font-bold">
                  {" "}
                  · taux {(pinStats.failure_ratio * 100).toFixed(0)} %
                </span>
              )}
            </p>
          )}
        </Card>

        {/* Cinquième quadrant : l'IA/DATA n'apparaissait qu'en `curl`. */}
        <Card title="Assistant RAG — dossier technique" className="md:col-span-2">
          <RagAssistant />
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
          <AnsibleConsole state={systemState.state} detail={detail} />
          <div className="mt-3 pt-3 border-t border-rule">
            <p className="text-ink/70 text-xs font-bold uppercase tracking-wide">
              Audit view-only (DORA art. 19)
            </p>
            {audit?.available ? (
              <>
                <p className="text-ink text-lg font-bold tabular-nums">
                  montant {audit.amount}
                </p>
                <p className="text-ink/60 text-[11px] font-mono break-all">
                  {audit.output_id} · anneau {audit.ring_size} · key image{" "}
                  {String(audit.key_image).slice(0, 12)}…
                </p>
                <p className="text-ink/50 text-[11px] mt-1">
                  déchiffré sans la Spend Key : aucune capacité de signature
                </p>
              </>
            ) : (
              <p className="text-ink/50 text-xs">
                {audit?.reason ?? "en attente d'une transaction confirmée"}
              </p>
            )}
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

  if (checking) {
    return (
      <div className="min-h-screen bg-page flex items-center justify-center">
        <p className="text-ink font-bold">Vérification de la session…</p>
      </div>
    );
  }

  // Le back-office exige une session : sans elle, on retombe sur la connexion.
  if (view === "login" || ((view === "supervision" || view === "admin") && !user)) {
    return (
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
  }

  if (view === "admin" && user) {
    return (
      <AdminPanel
        currentUser={user}
        onLogout={logout}
        onOpenShop={() => setView("shop-admin")}
      />
    );
  }

  if (view === "shop-admin" && user) {
    return (
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
  }

  if (view === "supervision" && user) {
    return (
      <Supervision
        user={user}
        onLogout={logout}
        onOpenAdmin={user.role === "admin" ? () => setView("admin") : undefined}
      />
    );
  }

  return (
    <Storefront
      user={user}
      onLogin={() => setView("login")}
      onSupervision={() => setView("supervision")}
      onAdmin={() => setView("admin")}
      onLogout={logout}
    />
  );
}
