#!/usr/bin/env python3
"""Préchauffe l'assistant RAG et Ollama avant la démonstration.

    python tools/warmup_rag.py                 # PC Hôte, pile démarrée
    python tools/warmup_rag.py --host 10.0.0.5 # depuis un autre poste

Pourquoi ce script existe : le **premier** appel à Ollama charge le modèle en
mémoire (4,9 Go, 8 à 12 s). Si le jury pose sa première question à ce
moment-là, il voit un écran figé et un `engine: ollama:...` qui arrive en retard
— ou, si le délai est dépassé, un repli extractif qu'il faut ensuite expliquer.

Le script pose donc deux questions : la première paie le chargement, la seconde
mesure la latence réelle. Il vérifie aussi que le moteur annoncé est bien
`ollama:<modèle>` et que les sources citées existent.

Code de sortie : 0 si l'assistant répond avec Ollama, 2 s'il répond en mode
extractif (dégradé mais utilisable), 1 si le service est injoignable.
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

QUESTIONS = (
    "combien de temps prend la reprise apres une attaque ?",
    "comment le stock est-il reserve lors d'un achat ?",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost", help="hôte du PC qui porte la pile")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    base = f"http://{args.host}:8091"
    try:
        health = httpx.get(f"{base}/healthz", timeout=5.0).json()
    except httpx.HTTPError as exc:
        print(f"ECHEC : assistant RAG injoignable ({base}) : {exc}")
        print("  → docker compose up -d rag-assistant")
        return 1

    index = health.get("index", {})
    print(f"index : {index.get('documents')} documents, {index.get('chunks')} passages")
    print(f"Ollama : {health.get('generation', {}).get('ollama', {}).get('state')}")

    engines = []
    for number, question in enumerate(QUESTIONS, start=1):
        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{base}/ask", json={"q": question}, timeout=args.timeout
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"ECHEC sur la question {number} : {exc}")
            return 1
        body = response.json()
        elapsed = time.perf_counter() - started
        engines.append(body.get("engine", "?"))
        sources = body.get("sources") or []
        print(
            f"question {number} : {body.get('engine')} · {elapsed:.1f} s · "
            f"{len(sources)} source(s)"
        )
        if sources:
            first = sources[0]
            print(f"  {first.get('source')} §{first.get('heading')}")

    if all(engine.startswith("ollama:") for engine in engines):
        print("\nPRÊT : le modèle répond. La question du jury ne paiera plus le chargement.")
        return 0
    print(
        "\nMODE DÉGRADÉ : le service répond en extractif (il cite le dossier).\n"
        "  Vérifier : brew services list | grep ollama   (macOS)\n"
        "             ollama list                       (le modèle est-il téléchargé ?)\n"
        "             curl -s localhost:11434/api/tags"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
