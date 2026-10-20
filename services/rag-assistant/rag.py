"""RAG local — index BM25 construit au build, génération Ollama optionnelle.

Pourquoi pas LangChain : la chaîne utile ici tient en deux étapes (récupérer,
générer). LangChain ajouterait une dépendance lourde et une couche
d'indirection pour deux appels HTTP. On utilise donc l'API d'Ollama
directement, et un BM25 écrit en 40 lignes — sans numpy, sans modèle à
télécharger au runtime.

Ce que ça garantit :

* l'index est construit **à la construction de l'image**, jamais au runtime :
  aucun téléchargement, aucune indexation au premier appel ;
* si Ollama n'est pas joignable, le service répond en **extractif** en citant
  ses sources, au lieu de renvoyer une erreur. Un assistant muet en démo est
  pire qu'un assistant qui cite le bon paragraphe ;
* chaque réponse porte ses sources : un juré peut vérifier la phrase citée.

Le modèle visé est `llama3.1:8b` (≤ 8 Md de paramètres, comme demandé). Il
tourne sur le PC IA, pas dans la pile Docker : `OLLAMA_URL` pointe vers lui.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

TOKEN_RE = re.compile(r"[a-z0-9]+")
# Mots vides français/anglais : ils dominent le score BM25 sans porter de sens.
STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "du", "de", "d", "et", "ou", "à",
    "au", "aux", "en", "dans", "sur", "pour", "par", "avec", "sans", "est",
    "sont", "ce", "cet", "cette", "ces", "il", "elle", "ils", "elles", "on",
    "que", "qui", "quoi", "dont", "où", "ne", "pas", "plus", "moins", "se",
    "sa", "son", "ses", "leur", "leurs", "nous", "vous", "je", "tu", "y",
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "be", "it", "this", "that", "as", "at", "by", "with", "from",
}
CHUNK_CHARS = 900
CHUNK_OVERLAP = 150


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def tokenize(text: str) -> list[str]:
    return [
        token
        for token in TOKEN_RE.findall(normalize(text))
        if len(token) > 1 and token not in STOPWORDS
    ]


@dataclass
class Chunk:
    source: str
    heading: str
    text: str
    tokens: list[str]

    @property
    def length(self) -> int:
        return len(self.tokens)


def _split_sections(markdown: str) -> list[tuple[str, str]]:
    """Découpe un document en (titre de section, texte)."""
    sections: list[tuple[str, str]] = []
    heading = "introduction"
    buffer: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("#"):
            if buffer:
                sections.append((heading, "\n".join(buffer).strip()))
                buffer = []
            heading = line.lstrip("#").strip() or heading
        else:
            buffer.append(line)
    if buffer:
        sections.append((heading, "\n".join(buffer).strip()))
    return [(title, body) for title, body in sections if body]


def _window(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        # Coupe sur une frontière de phrase quand c'est possible.
        if end < len(text):
            cut = text.rfind(". ", start, end)
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [chunk for chunk in chunks if chunk]


EXCLUDED_DIRS = {
    "node_modules", ".venv", "venv", "dist", "build", ".pio", "target",
    "__pycache__", ".git", ".pytest_cache", "local-dev",
}


def build_index(corpus_dir: Path) -> dict:
    """Construit l'index à partir d'un dossier de documents Markdown.

    Les dossiers de dépendances (`node_modules`, `.venv`, `dist`…) sont exclus :
    sinon le README d'un paquet npm se classe mieux que le dossier technique,
    et l'assistant cite `postcss-value-parser` en Q&A. Constaté en recette.
    """
    documents = sorted(
        path
        for path in corpus_dir.rglob("*.md")
        if not EXCLUDED_DIRS & set(path.relative_to(corpus_dir).parts)
    )
    if not documents:
        raise SystemExit(f"aucun document Markdown dans {corpus_dir}")

    chunks: list[Chunk] = []
    for document in documents:
        relative = document.relative_to(corpus_dir).as_posix()
        for heading, body in _split_sections(document.read_text(encoding="utf-8")):
            for piece in _window(body):
                chunks.append(
                    Chunk(
                        source=relative,
                        heading=heading,
                        text=piece,
                        tokens=tokenize(f"{heading} {piece}"),
                    )
                )

    document_frequency: dict[str, int] = {}
    for chunk in chunks:
        for token in set(chunk.tokens):
            document_frequency[token] = document_frequency.get(token, 0) + 1

    average_length = sum(chunk.length for chunk in chunks) / max(1, len(chunks))
    return {
        "version": 1,
        "documents": len(documents),
        "average_length": round(average_length, 3),
        "chunks": [
            {
                "source": chunk.source,
                "heading": chunk.heading,
                "text": chunk.text,
                "length": chunk.length,
            }
            for chunk in chunks
        ],
        "document_frequency": document_frequency,
    }


class Retriever:
    """BM25 (k1=1.5, b=0.75) — le même classement qu'un moteur de recherche."""

    def __init__(self, index: dict):
        self.chunks = index["chunks"]
        self.average_length = float(index.get("average_length") or 1.0)
        self.document_frequency = index.get("document_frequency", {})
        self.total = len(self.chunks)
        self._tokens = [tokenize(f"{chunk['heading']} {chunk['text']}") for chunk in self.chunks]

    def _idf(self, token: str) -> float:
        df = self.document_frequency.get(token, 0)
        return math.log(1.0 + (self.total - df + 0.5) / (df + 0.5))

    def search(self, query: str, limit: int = 4) -> list[dict]:
        terms = tokenize(query)
        if not terms:
            return []
        k1, b = 1.5, 0.75
        scored: list[tuple[float, int]] = []
        for position, tokens in enumerate(self._tokens):
            if not tokens:
                continue
            counts: dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            score = 0.0
            for term in terms:
                frequency = counts.get(term, 0)
                if frequency == 0:
                    continue
                denominator = frequency + k1 * (
                    1 - b + b * len(tokens) / self.average_length
                )
                score += self._idf(term) * frequency * (k1 + 1) / denominator
            if score > 0:
                scored.append((score, position))

        scored.sort(reverse=True)
        results = []
        for score, position in scored[:limit]:
            chunk = self.chunks[position]
            results.append(
                {
                    "source": chunk["source"],
                    "heading": chunk["heading"],
                    "text": chunk["text"],
                    "score": round(score, 3),
                }
            )
        return results


def load_index(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _row_as_sentence(line: str) -> str | None:
    """Transforme une ligne de tableau Markdown en phrase lisible.

    « | Durée de la chaîne P0 | 6,6 à 7,2 s (limite 90 s) | » devient
    « Durée de la chaîne P0 : 6,6 à 7,2 s (limite 90 s) ». Sans cela, la
    réponse à « combien de temps prend la reprise ? » serait un fragment de
    tableau, illisible à l'oral comme à l'écrit.
    """
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    cells = [cell for cell in cells if cell and not set(cell) <= {"-", ":", " "}]
    if len(cells) < 2:
        return None
    label, *values = cells
    return f"{label} : {' '.join(values)}"


def extractive_answer(question: str, results: list[dict]) -> str:
    """Réponse sans LLM : les phrases qui contiennent les termes de la question.

    Ce n'est pas une réponse « inventée » : ce sont les phrases du dossier,
    citées telles quelles, avec leur source. C'est ce qui rend le mode dégradé
    défendable en Q&A.

    Les lignes de code et de commande sont écartées : ce sont des fragments qui
    se lisent mal hors contexte. Les **lignes de tableau** sont converties en
    phrases quand la question attend une valeur (« combien de temps… ») : c'est
    précisément là que vivent les mesures du dossier.
    """
    terms = set(tokenize(question))
    if not results:
        return "Aucun passage du dossier ne répond à cette question."

    # Question de durée (« combien de temps… ») : les phrases qui portent une
    # valeur en secondes sont celles qui répondent. Sans ce signal, BM25
    # remonte « entre l'attaque et la reprise » — vrai, mais hors sujet.
    numeric_hint = bool(
        terms & {"combien", "duree", "temps", "delai", "latence", "secondes", "minutes"}
    )
    numeric_re = re.compile(r"\d+(?:[.,]\d+)?\s*(?:s\b|ms\b|min\b|secondes?\b|minutes?\b)")

    def usable(sentence: str) -> bool:
        stripped = sentence.lstrip()
        if len(stripped) < 40:
            return False
        if stripped.startswith(
            ("#", ">", "```", "-", "*", "//", "curl", "docker", "python", "../")
        ):
            return False
        if stripped.count("|") >= 2:
            return False
        return True

    candidates: list[tuple[str, str]] = []
    for result in results:
        # Les documents sont coupés à 80 colonnes : couper sur `\n` casserait
        # les phrases en plein milieu (« … transaction de preuve — prend »).
        # On recolle donc les lignes d'un même paragraphe, puis on sépare sur
        # les phrases. Les blocs de tableau restent ligne par ligne.
        for paragraph in re.split(r"\n\s*\n", result["text"]):
            lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
            if not lines:
                continue
            is_table = all(line.startswith("|") for line in lines)
            if is_table:
                if not numeric_hint:
                    continue
                for line in lines:
                    sentence = _row_as_sentence(line)
                    if sentence is None or not numeric_re.search(sentence):
                        continue
                    candidates.append((sentence, result["source"]))
                continue
            candidates.append((" ".join(lines), result["source"]))

    # Phrases reconstituées : un paragraphe peut contenir plusieurs phrases.
    sentences: list[tuple[str, str]] = []
    for text, source in candidates:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            sentence = sentence.strip()
            if usable(sentence):
                sentences.append((sentence, source))
    candidates = sentences

    # Un passage de tableau n'a pas de rang individuel : on garde l'ordre de
    # pertinence du passage qui le contient, déjà porté par `results`.
    scored: list[tuple[float, str, str]] = []
    rank_of = {}
    for position, result in enumerate(results):
        for sentence, source in candidates:
            if source == result["source"] and sentence in result["text"].replace(
                "\n", " "
            ):
                rank_of.setdefault(sentence, position)

    for sentence, source in candidates:
        rank = rank_of.get(sentence, len(results) - 1)
        overlap = len(terms & set(tokenize(sentence)))
        carries_number = bool(numeric_re.search(sentence))
        if not overlap and not (numeric_hint and carries_number and rank <= 2):
            continue
        # Priorité : couverture de la question, puis rang du passage, puis
        # longueur (une phrase complète informe mieux qu'un fragment).
        score = overlap * 10 + min(len(sentence), 300) / 100 - rank
        if numeric_hint and carries_number:
            # Une question « combien de temps » attend un nombre : la phrase
            # qui en porte un passe devant, même si elle répète moins de mots.
            score += 30
        scored.append((score, sentence, source))

    scored.sort(reverse=True)

    if not scored:
        best = results[0]
        return f"D'après `{best['source']}` (§{best['heading']}) : {best['text'][:400]}"

    picked: list[tuple[str, str]] = []
    seen_sources: set[str] = set()
    for _, sentence, source in scored:
        if sentence in {text for text, _ in picked}:
            continue
        picked.append((sentence, source))
        seen_sources.add(source)
        if len(picked) >= 3 or len(seen_sources) >= 2:
            break

    return " ".join(f"{sentence} [{source}]" for sentence, source in picked)


# Ollama est un service EXTERNE à la pile (PC IA). Il peut être éteint, lent,
# ou derrière un réseau qui absorbe les connexions. Sans garde-fou, chaque
# `/ask` attendait son timeout : mesuré à 2 s de blocage quand Ollama est
# injoignable, et 45 s avec un hôte qui ne répond pas. Un assistant qui gèle
# le dashboard pendant 45 s est une panne, pas une fonctionnalité.
#
# Trois protections :
#   1. délais séparés : connexion courte (2 s), lecture longue (le modèle
#      rédige, c'est normal) ;
#   2. disjoncteur : après un échec, on n'essaie plus pendant 30 s ;
#   3. le mode extractif répond immédiatement.
OLLAMA_CONNECT_TIMEOUT = 2.0
OLLAMA_BREAKER_SECONDS = 30.0
_breaker = {"down_until": 0.0, "last_error": None}


def ollama_available(cfg: dict) -> bool:
    """État du disjoncteur. Ne fait AUCUN appel réseau : instantané."""
    return time.time() >= _breaker["down_until"]


def _trip_breaker(error: str) -> None:
    _breaker["down_until"] = time.time() + OLLAMA_BREAKER_SECONDS
    _breaker["last_error"] = error


def ollama_generate(question: str, results: list[dict], cfg: dict) -> str | None:
    """Génère une réponse avec Ollama. Retourne None si Ollama est muet.

    Ne lève jamais : le mode extractif prend le relais, et `/healthz` dit
    pourquoi.
    """
    import httpx

    if not ollama_available(cfg):
        return None

    context = "\n\n".join(
        f"[{result['source']} §{result['heading']}]\n{result['text']}" for result in results
    )
    prompt = (
        "Tu es l'assistant technique du projet RESILIENCE-ALPHA. Réponds en "
        "français, en trois phrases maximum, UNIQUEMENT à partir des extraits "
        "fournis. Cite la source entre crochets après chaque affirmation "
        "(par exemple [docs/RAPPORT.md]). Si les extraits ne contiennent pas la "
        "réponse, dis-le explicitement.\n\n"
        f"Extraits :\n{context}\n\nQuestion : {question}\nRéponse :"
    )
    try:
        response = httpx.post(
            f"{cfg['url'].rstrip('/')}/api/generate",
            json={
                "model": cfg["model"],
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 220},
            },
            timeout=httpx.Timeout(cfg["timeout"], connect=OLLAMA_CONNECT_TIMEOUT),
        )
        response.raise_for_status()
        answer = (response.json().get("response") or "").strip()
        _breaker["last_error"] = None
        return answer or None
    except Exception as exc:  # noqa: BLE001 - le mode dégradé est explicite
        _trip_breaker(f"{type(exc).__name__}")
        return None


def ollama_config() -> dict:
    return {
        "url": os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434"),
        "model": os.environ.get("OLLAMA_MODEL", "llama3.1:8b"),
        "timeout": float(os.environ.get("OLLAMA_TIMEOUT", "45")),
    }


def ollama_health(cfg: dict) -> dict:
    """État pour `/healthz`, sans bloquer : lit le disjoncteur, pas le réseau."""
    if not ollama_available(cfg):
        remaining = round(_breaker["down_until"] - time.time(), 1)
        return {
            "state": "unreachable",
            "last_error": _breaker["last_error"],
            "retry_in_seconds": max(0.0, remaining),
        }
    return {"state": "unknown", "last_error": _breaker["last_error"]}
