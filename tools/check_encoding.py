"""Contrôle d'encodage : aucun mojibake dans les fichiers livrés.

Utilisé comme garde-fou avant un commit : un fichier UTF-8 relu par une console
Windows (cp1252) et réécrit produit « automatisés » -> « automatisÃ©s ». C'est
arrivé une fois, et cela rend la documentation illisible pour le jury.

    python tools/check_encoding.py        # code de sortie 1 si corruption
"""
from __future__ import annotations

import io
import pathlib
import sys

SUFFIXES = (".md", ".py", ".jsx", ".js", ".yml", ".rs", ".cpp", ".h", ".txt", ".sh", ".yml", ".toml")
SKIP = ("node_modules", ".venv", "dist", "target", ".pio", "__pycache__", ".git")
MARKERS = ("Ã©", "Ã¨", "Ã ", "Ã§", "Ãª", "Ã®", "Ã´", "Ã»", "â€”", "â€™", "â‚¬", "Ã‰", "Ã€")


def main() -> int:
    broken = 0
    for path in pathlib.Path(".").rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if any(part in SKIP for part in path.parts):
            continue
        # Ce fichier-ci contient les marqueurs par construction.
        if path.name in {"check_encoding.py", "fix_encoding.py"}:
            continue
        text = io.open(path, encoding="utf-8", errors="replace").read()
        count = sum(text.count(marker) for marker in MARKERS)
        if count:
            print(f"{path}: {count} séquence(s) de mojibake")
            broken += 1
    if broken:
        print(f"\n{broken} fichier(s) corrompu(s). Réparer : python tools/fix_encoding.py --apply")
        return 1
    print("Encodage propre : aucun mojibake.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
