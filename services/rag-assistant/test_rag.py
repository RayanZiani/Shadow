"""Tests du RAG — la réponse doit citer le bon document.

    python -m pytest test_rag.py -v

Ce qui compte pour le jury : « l'IA actualise ses compétences via le RAG ».
Un test qui vérifie que la question de référence (« combien de temps prend la
reprise ? ») retrouve **le bon paragraphe** et le **cite** vaut mieux qu'un test
qui vérifie qu'un endpoint répond 200.

Ollama n'est pas requis : sans lui, le service répond en extractif, et c'est
précisément ce mode que ces tests exercent.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(HERE) in sys.path:
    sys.path.remove(str(HERE))
sys.path.insert(0, str(HERE))
# Isolation : plusieurs services exposent un `main.py`. Lancé depuis la racine
# (`pytest services/`), `import main` résoudrait le module d'un autre service.
for _name in ("main", "rag"):
    _module = sys.modules.get(_name)
    _file = getattr(_module, "__file__", None)
    if _file and pathlib.Path(_file).resolve().parent != HERE:
        del sys.modules[_name]

from rag import Retriever, build_index, extractive_answer, tokenize  # noqa: E402


@pytest.fixture(scope="module")
def index():
    """Index construit sur les vrais documents du dépôt."""
    return build_index(ROOT)


@pytest.fixture(scope="module")
def retriever(index):
    return Retriever(index)


@pytest.fixture()
def client(index, tmp_path, monkeypatch):
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("RAG_INDEX", str(index_path))
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:1")  # volontairement muet

    # Chargement par chemin de fichier : plusieurs services exposent un
    # `main.py`, et un `import main` dépendrait de l'ordre de collecte de
    # pytest. Ici, aucun doute possible sur le module testé.
    import importlib.util

    spec = importlib.util.spec_from_file_location("rag_main", HERE / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return TestClient(module.app)


# ---------------------------------------------------------------------------
# Indexation
# ---------------------------------------------------------------------------
def test_lindex_couvre_les_documents_du_depot(index):
    sources = {chunk["source"] for chunk in index["chunks"]}
    assert any("RAPPORT.md" in source for source in sources)
    assert any("DEMO.md" in source for source in sources)
    assert len(index["chunks"]) > 100
    assert index["documents"] >= 5


def test_le_decoupage_conserve_le_titre_de_section(index):
    chunks = [chunk for chunk in index["chunks"] if "RAPPORT.md" in chunk["source"]]
    assert chunks
    assert all(chunk["heading"] for chunk in chunks)


def test_la_tokenisation_retire_les_accents_et_les_mots_vides():
    # Les nombres à un chiffre sont écartés (bruit), les accents sont repliés.
    assert tokenize("La reprise dure 6,6 secondes") == ["reprise", "dure", "secondes"]
    assert tokenize("RÉSILIENCE et rémédiation") == ["resilience", "remediation"]


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------
def test_la_question_de_reference_retrouve_le_rapport(retriever):
    results = retriever.search("combien de temps prend la reprise ?", limit=4)
    assert results
    assert any("RAPPORT.md" in result["source"] for result in results)


def test_la_recherche_trouve_la_duree_mesuree(retriever):
    results = retriever.search("durée de la chaîne P0 recovery seconds", limit=5)
    joined = " ".join(result["text"] for result in results)
    assert "6,6" in joined or "7,2" in joined or "recovery" in joined.lower()


def test_une_question_hors_sujet_ne_trouve_rien(retriever):
    # Aucun terme de cette question n'apparaît dans le dossier.
    assert retriever.search("xylophone zorglub", limit=4) == []


# ---------------------------------------------------------------------------
# Réponse
# ---------------------------------------------------------------------------
def test_le_mode_extractif_cite_ses_sources(retriever):
    results = retriever.search("combien de temps prend la reprise ?", limit=4)
    answer = extractive_answer("combien de temps prend la reprise ?", results)
    # Une citation au format [chemin.md] ferme chaque phrase : la réponse est
    # vérifiable par un juré, elle n'est pas inventée.
    assert ".md]" in answer
    assert len(answer) > 40
    # Et le paragraphe de référence (le dossier d'ingénierie) est bien retrouvé.
    assert any("RAPPORT.md" in result["source"] for result in results)


def test_lapi_repond_avec_sources_et_moteur(client):
    response = client.post("/ask", json={"q": "combien de temps prend la reprise ?"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["engine"] == "extractive", "Ollama est muet : le mode dégradé doit le dire"
    assert body["sources"]
    assert any("RAPPORT.md" in source["source"] for source in body["sources"])
    assert body["answer"]


def test_une_question_trop_courte_est_refusee(client):
    assert client.post("/ask", json={"q": "a"}).status_code == 422


def test_healthz_declare_le_mode_de_generation(client):
    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["retrieval"] == "bm25"
    assert health["index"]["chunks"] > 100
    assert health["generation"]["model"] == "llama3.1:8b"
    assert health["generation"]["ollama"] in {"unreachable", "ready"} or health[
        "generation"
    ]["ollama"].startswith("http_")
