import { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8080";

// Badge d'état : vert émeraude = normal, rouge cramoisi = attaque (charte imposée, slide 9)
function StatusBadge({ underAttack }) {
  return (
    <span
      className={`px-3 py-1 rounded-full text-sm font-bold text-white ${
        underAttack ? "bg-crimson" : "bg-emerald"
      }`}
    >
      {underAttack ? "SOUS ATTAQUE" : "NORMAL"}
    </span>
  );
}

function Card({ title, children }) {
  return (
    <div className="bg-card rounded-xl p-5 h-full">
      <h2 className="text-ink font-bold mb-3">{title}</h2>
      {children}
    </div>
  );
}

export default function App() {
  const [probeStatus, setProbeStatus] = useState(null);
  const [underAttack, setUnderAttack] = useState(false);

  useEffect(() => {
    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/tx/auto-probe/status`);
        const data = await res.json();
        setProbeStatus(data);
        setUnderAttack(!data.ok);
      } catch {
        // TODO (vous) : afficher un état "déconnecté" visible plutôt que de rester muet
      }
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="min-h-screen bg-page p-6">
      <header className="flex items-center justify-between mb-6">
        <h1 className="text-ink text-2xl font-bold">RESILIENCE-ALPHA — SHADOW-SETTLE</h1>
        <StatusBadge underAttack={underAttack} />
      </header>

      {/* Les 4 quadrants exigés pour le Clip Chrono, lisibles à distance */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <Card title="Flux transactionnel Monero live">
          {/* TODO : brancher sur un flux SSE/WebSocket de payment-api,
              afficher key image, taille d'anneau N, statut auto-probe */}
          <p className="text-ink text-sm italic">
            Dernière sonde : {probeStatus ? new Date(probeStatus.last_run * 1000).toLocaleTimeString() : "…"}
          </p>
        </Card>

        <Card title="Radar machine learning">
          {/* TODO : courbe temps réel du score d'anomalie (ml-detector /ingest)
              + entropie de la decoy pool. Pas de seuil fixe affiché en dur. */}
          <p className="text-ink text-sm italic">Score d'anomalie en direct — à connecter</p>
        </Card>

        <Card title="Topologie des 5 piliers & anneau visuel">
          {/* TODO : anneau SVG/Canvas qui tourne en sync avec le HSM physique
              via l'état reçu de remediation-engine */}
          <p className="text-ink text-sm italic">Anneau synchronisé avec le hardware — à connecter</p>
        </Card>

        <Card title="Console de remédiation & conformité DORA">
          {/* TODO : lire remediation-engine:/history et rag-assistant pour
              afficher le playbook Ansible en cours + la fiche DORA générée */}
          <p className="text-ink text-sm italic">Journal Ansible + rapport DORA — à connecter</p>
        </Card>
      </div>
    </div>
  );
}
