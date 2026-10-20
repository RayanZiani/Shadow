#!/usr/bin/env python3
"""Construit l'index RAG à partir des documents du dépôt.

    python build_index.py --corpus ../../docs --out index.json
    python build_index.py --corpus . --out index.json   # docs + README

Appelé **à la construction de l'image** (`services/rag-assistant/Dockerfile`),
jamais au runtime : l'assistant démarre avec son index déjà en mémoire, donc
aucune indexation au premier appel et aucun téléchargement le jour J.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rag import build_index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, help="dossier contenant les .md")
    parser.add_argument("--out", default="index.json")
    args = parser.parse_args()

    corpus = Path(args.corpus)
    if not corpus.exists():
        print(f"corpus introuvable : {corpus}")
        return 2

    index = build_index(corpus)
    Path(args.out).write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"index écrit : {args.out} · {index['documents']} documents · "
        f"{len(index['chunks'])} passages · {len(index['document_frequency'])} termes"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
