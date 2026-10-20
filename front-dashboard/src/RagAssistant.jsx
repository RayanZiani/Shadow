import { useState } from "react";

/**
 * Assistant RAG — répond sur le dossier technique, avec ses sources.
 *
 * Pourquoi ce quadrant : le RAG existait en `curl` seulement. Un livrable qui
 * n'apparaît pas à l'écran n'existe pas pour un jury qui regarde une
 * démonstration. Ici, la question de référence se pose devant tout le monde.
 *
 * Deux choses sont affichées volontairement :
 *
 * 1. **le moteur** (`ollama:llama3.1:8b` ou `extractive`). Si Ollama est
 *    éteint, l'assistant le DIT au lieu de faire semblant. Un assistant qui
 *    cite les phrases du dossier reste vrai ; un assistant qui invente serait
 *    pire qu'un assistant muet.
 * 2. **les sources**, avec leur score BM25. Un juré peut vérifier la phrase
 *    citée dans le document, en direct.
 */

const RAG_BASE = import.meta.env.VITE_RAG_BASE || "http://localhost:8091";

const EXAMPLES = [
  "combien de temps prend la reprise ?",
  "quelles failles ont été fermées ?",
  "comment est protégé le stock de la boutique ?",
  "comment l'IA agit sur une alerte ?",
];

export default function RagAssistant() {
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const ask = async (value) => {
    const query = (value ?? question).trim();
    if (query.length < 3) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${RAG_BASE}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ q: query }),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      setResult(await response.json());
    } catch (cause) {
      setError(
        `assistant injoignable (${RAG_BASE}) — docker compose up -d rag-assistant`
      );
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const extractive = result?.engine === "extractive";

  return (
    <div>
      <div className="flex gap-2">
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") ask();
          }}
          placeholder="poser une question sur le dossier…"
          className="flex-1 rounded-lg border border-rule bg-white px-3 py-2 text-sm text-ink"
        />
        <button
          onClick={() => ask()}
          disabled={busy || question.trim().length < 3}
          className="bg-ink text-white text-sm font-bold rounded-lg px-3 py-2 disabled:opacity-40"
        >
          {busy ? "…" : "Demander"}
        </button>
      </div>

      <div className="flex flex-wrap gap-1 mt-2">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            onClick={() => {
              setQuestion(example);
              ask(example);
            }}
            disabled={busy}
            className="text-[10px] text-ink/60 underline decoration-dotted disabled:opacity-40"
          >
            {example}
          </button>
        ))}
      </div>

      {error && <p className="text-crimson text-xs font-bold mt-2">{error}</p>}

      {result && (
        <div className="mt-3">
          <p className="text-ink text-sm leading-snug whitespace-pre-wrap">
            {result.answer}
          </p>

          <p className="mt-2 text-[10px] font-mono text-ink/50">
            moteur {result.engine} · {result.latency_ms} ms
            {extractive && (
              <span className="text-amber-700">
                {" "}
                · mode dégradé : Ollama muet, réponse extraite du dossier
              </span>
            )}
          </p>

          {result.sources.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {result.sources.map((source, index) => (
                <li
                  key={`${source.source}-${index}`}
                  className="text-[10px] font-mono text-ink/60 truncate"
                  title={source.snippet}
                >
                  {source.source} §{source.heading} · score {source.score}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!result && !error && (
        <p className="mt-2 text-ink/50 text-[11px]">
          index BM25 du dossier (docs/ + README), construit au build de l'image ·
          chaque réponse cite ses sources
        </p>
      )}
    </div>
  );
}
