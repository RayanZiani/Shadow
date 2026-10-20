"""Répare le double encodage UTF-8 (mojibake) dans les fichiers texte.

Symptôme : « automatisés » devient « automatisÃ©s », « — » devient « â€” ».
Cause : un fichier UTF-8 relu comme du texte 8 bits (cp1252 ou latin-1, selon
la console Windows) puis réécrit en UTF-8.

Pourquoi un décodage « mixte » : selon la page de code de la console, les octets
0x80–0x9F sont soit des caractères cp1252 (€, ”, …), soit des caractères de
contrôle U+0080–U+009F. Les deux variantes apparaissent dans un même fichier
corrompu à deux moments différents. On accepte donc les deux correspondances,
on reconstruit la suite d'octets d'origine, puis on la décode en UTF-8.

    python tools/fix_encoding.py            # diagnostic, n'écrit rien
    python tools/fix_encoding.py --apply    # répare
"""
from __future__ import annotations

import io
import pathlib
import sys

SUFFIXES = (".md", ".py", ".jsx", ".js", ".yml", ".rs", ".cpp", ".h", ".txt", ".json")
SKIP = ("node_modules", ".venv", "dist", "target", ".pio", "__pycache__", ".git")

# Marqueurs de mojibake : des séquences qu'on ne rencontre pas en français
# correct. Sert seulement au diagnostic (compter les dégâts).
MARKERS = ("Ã©", "Ã¨", "Ã ", "Ã§", "Ãª", "Ã®", "Ã´", "Ã»", "â€”", "â€™", "â€œ", "â‚¬")


def _reverse_cp1252() -> dict[str, int]:
    table = {}
    for byte in range(0x80, 0x100):
        try:
            table[bytes([byte]).decode("cp1252")] = byte
        except UnicodeDecodeError:
            continue
    return table


CP1252 = _reverse_cp1252()


def _to_bytes(text: str) -> bytes | None:
    """Reconstruit la suite d'octets d'origine, ou None si impossible."""
    out = bytearray()
    for char in text:
        code = ord(char)
        if code <= 0xFF:
            out.append(code)
        elif char in CP1252:
            out.append(CP1252[char])
        else:
            return None
    return bytes(out)


def repair(text: str) -> str:
    raw = _to_bytes(text)
    if raw is None:
        return text
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return text


def _files() -> list[pathlib.Path]:
    found = []
    for path in pathlib.Path(".").rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if any(part in SKIP for part in path.parts):
            continue
        found.append(path)
    return found


def main() -> int:
    apply = "--apply" in sys.argv
    touched = 0
    for path in _files():
        text = io.open(path, encoding="utf-8", errors="replace").read()
        markers = sum(text.count(marker) for marker in MARKERS)
        if not markers:
            continue
        fixed = repair(text)
        if fixed == text:
            print(f"{path}: {markers} marqueur(s), réparation impossible (octet inconnu)")
            continue
        remaining = sum(fixed.count(marker) for marker in MARKERS)
        print(f"{path}: {markers} marqueur(s) -> {remaining}")
        touched += 1
        if apply and remaining < markers:
            io.open(path, "w", encoding="utf-8", newline="").write(fixed)
    print(f"{touched} fichier(s) {'réparé(s)' if apply else 'à réparer'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
