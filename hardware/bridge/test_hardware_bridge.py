"""Tests du pont hardware — le contrat série et le découplage.

    python -m pytest test_hardware_bridge.py -v

Ce qui est réellement critique pour la démo (cf. docs/hardware/CONCEPT-PHYSIQUE.md §5) :

  * la trame envoyée à l'ESP32 est exactement `STATE:<NOM>` (le firmware
    n'accepte rien d'autre) ;
  * un état inconnu est ramené à `IDLE` au lieu de casser le parseur ;
  * le mode `--rich` ajoute SCORE et N sans changer le préfixe ;
  * le pont est STRICTEMENT en lecture : aucun POST vers remediation-engine.
"""
import ast
import pathlib

import pytest

import hardware_bridge as bridge


def test_trame_simple():
    assert bridge.build_frame({"state": "UNDER_ATTACK"}, rich=False) == "STATE:UNDER_ATTACK"


def test_etat_inconnu_ramene_a_idle():
    assert bridge.build_frame({"state": "HACKED"}, rich=False) == "STATE:IDLE"


def test_tous_les_etats_du_contrat_sont_acceptes():
    for state in bridge.VALID_STATES:
        assert bridge.build_frame({"state": state}, rich=False) == f"STATE:{state}"


def test_mode_riche_ajoute_score_et_taille_anneau():
    frame = bridge.build_frame(
        {"state": "UNDER_ATTACK", "detail": {"anomaly_score": 1.834, "ring_n": 11}},
        rich=True,
    )
    assert frame == "STATE:UNDER_ATTACK;SCORE:1.83;N:11"


def test_mode_riche_ignore_les_details_absents():
    assert bridge.build_frame({"state": "RECOVERED_TX", "detail": {}}, rich=True) == "STATE:RECOVERED_TX"


def test_mode_riche_resiste_a_un_detail_non_numerique():
    frame = bridge.build_frame(
        {"state": "UNDER_ATTACK", "detail": {"anomaly_score": "NaN", "ring_n": "x"}},
        rich=True,
    )
    assert frame == "STATE:UNDER_ATTACK"


def test_dry_run_n_ouvre_aucun_port_et_journalise(capsys):
    link = bridge.SerialLink(port=None, baud=115200, dry_run=True)
    link.send("STATE:NORMAL_TX")
    assert "DRY-RUN -> STATE:NORMAL_TX" in capsys.readouterr().out
    assert link._serial is None


def test_le_pont_n_ecrit_jamais_vers_le_moteur():
    """Aucun POST/PUT : le terminal ne peut pas déclencher de remédiation."""
    source = pathlib.Path(bridge.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("post", "put", "delete", "patch"), (
                f"appel {node.func.attr} détecté dans le pont hardware"
            )
        # urllib.request.Request sans method= (donc GET)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Request":
            keywords = {kw.arg for kw in node.keywords}
            assert "data" not in keywords and "method" not in keywords, (
                "le pont hardware envoie une requête non-GET"
            )


def test_replay_rejoue_la_sequence_complete_du_clip(monkeypatch):
    """La séquence de répétition doit couvrir les 4 états visibles du clip."""
    sent: list[str] = []

    class Stop(Exception):
        pass

    class Link:
        def send(self, frame: str) -> None:
            sent.append(frame)
            if len(sent) == 4:
                raise Stop

    monkeypatch.setattr(bridge.time, "sleep", lambda _: None)
    with pytest.raises(Stop):
        bridge.replay(Link())
    assert sent == [
        "STATE:NORMAL_TX",
        "STATE:UNDER_ATTACK",
        "STATE:REMEDIATING",
        "STATE:RECOVERED_TX",
    ]
