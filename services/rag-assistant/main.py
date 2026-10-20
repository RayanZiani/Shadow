"""Assistant RAG local — répond sur le dossier technique, avec ses sources.

    POST /ask {"q": "combien de temps prend la reprise ?"}

Réponse :
    {"answer": "... [docs/RAPPORT.md]", "sources": [...], "engine": "ollama:llama3.1:8b"}

Deux modes, tous deux tracés dans `/healthz` :

* `ollama` : le modèle (`llama3.1:8b`, ≤ 8 Md de paramètres) tourne sur le PC
  IA ; on lui donne les passages retrouvés et il rédige la réponse ;
* `extractive` : Ollama n'est pas joignable, on renvoie les phrases du dossier
  qui répondent, avec leur source. **Ce n'est pas une panne** : c'est un mode
  dégradé qui reste vrai. Un assistant qui invente serait pire.

L'index est construit au build de l'image (BM25 sur `docs/` + `README.md`).
Aucune indexation au runtime, aucun téléchargement.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from rag import (
    Retriever,
    extractive_answer,
    load_index,
    ollama_config,
    ollama_generate,
    ollama_health,
)

INDEX_PATH = os.environ.get("RAG_INDEX", str(Path(__file__).parent / "index.json"))
TOP_K = int(os.environ.get("RAG_TOP_K", "4"))

index = load_index(INDEX_PATH) if Path(INDEX_PATH).exists() else None
retriever = Retriever(index) if index else None

app = FastAPI(title="rag-assistant", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class AskRequest(BaseModel):
    q: str = Field(min_length=3, max_length=500)
    top_k: int = Field(default=TOP_K, ge=1, le=10)


def _ollama_status() -> dict:
    """État d'Ollama pour `/healthz` — SANS appel réseau bloquant.

    La version précédente interrogeait Ollama à chaque `/healthz` avec un
    timeout de 1,5 s : quand Ollama est injoignable, la sonde de santé du
    service mettait 1,5 s à répondre. Prometheus scrape toutes les 10 s et
    Docker vérifie le conteneur toutes les 10 s : c'était une fausse lenteur
    permanente. On lit désormais le disjoncteur, qui est instantané.
    """
    return ollama_health(ollama_config())


@app.get("/healthz")
def healthz():
    cfg = ollama_config()
    return {
        "status": "ok" if retriever else "degraded",
        "service": "rag-assistant",
        "index": {
            "path": INDEX_PATH,
            "documents": index["documents"] if index else 0,
            "chunks": len(index["chunks"]) if index else 0,
        },
        "retrieval": "bm25",
        "generation": {
            "model": cfg["model"],
            "ollama_url": cfg["url"],
            "ollama": _ollama_status(),
            "fallback": "extractive (cite les sources, n'invente pas)",
        },
        "index_built_at": "image" if index else "missing",
    }


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP rag_index_chunks Nombre de passages indexés",
        "# TYPE rag_index_chunks gauge",
        f"rag_index_chunks {len(index['chunks']) if index else 0}",
        "# HELP rag_index_documents Nombre de documents indexés",
        "# TYPE rag_index_documents gauge",
        f"rag_index_documents {index['documents'] if index else 0}",
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.post("/ask")
def ask(req: AskRequest):
    if retriever is None:
        raise HTTPException(
            status_code=503,
            detail="index absent : reconstruire l'image (build_index.py)",
        )
    started = time.perf_counter()
    results = retriever.search(req.q, limit=req.top_k)
    if not results:
        return {
            "answer": "Aucun passage du dossier ne traite cette question.",
            "sources": [],
            "engine": "none",
            "latency_ms": 0.0,
        }

    cfg = ollama_config()
    generated = ollama_generate(req.q, results, cfg)
    if generated:
        engine = f"ollama:{cfg['model']}"
        answer = generated
    else:
        engine = "extractive"
        answer = extractive_answer(req.q, results)

    return {
        "answer": answer,
        "sources": [
            {
                "source": result["source"],
                "heading": result["heading"],
                "score": result["score"],
                "snippet": result["text"][:280],
            }
            for result in results
        ],
        "engine": engine,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8091)
