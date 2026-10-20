"""Tests de l'API de paiement — le modèle de menace qui alimente le ML.

    python -m pytest test_payment_api.py -v

Ces tests couvrent les propriétés que la démonstration exploite réellement :

  * `/decoys` tire selon une gamma biaisée récents (et non uniformément) ;
  * `/outputs?unspent=true` ne renvoie jamais un output déjà dépensé ;
  * le registre se réalimente (deux démos d'affilée ne l'épuisent pas) ;
  * `/admin/quarantine-outputs` retire les outputs les plus ANCIENS (le signal
    mesuré par le ML), et non 25 outputs au hasard ;
  * `/admin/ring-size` change réellement la taille d'anneau par défaut ;
  * un PIN refusé est compté et n'ouvre aucun droit.

La chaîne cryptographique (signature, key image, audit view-only) est testée
séparément dans `test_crypto_flow.py`, qui parle au vrai noyau Rust.
"""
import statistics

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client(monkeypatch):
    # Pas de réseau dans les tests unitaires : télémétrie, miroir d'état et
    # émission d'outputs sont remplacés par des stubs déterministes.
    async def fake_send_telemetry(observation):
        return None

    monkeypatch.setattr(main, "_send_telemetry", fake_send_telemetry)

    async def fake_report(detail):
        return None

    monkeypatch.setattr(main, "_report_success_tx", fake_report)

    async def fake_mint(count, internal=False):
        for _ in range(count):
            main._registry_seq += 1
            output_id = f"mint_{main._registry_seq}"
            main.OUTPUTS[output_id] = {
                "id": output_id,
                "age_days": 3.0 if not internal else 0.01,
                "one_time_public_key_hex": "aa" * 32,
                "tx_public_key_hex": "bb" * 32,
                "encrypted_amount_hex": "cc" * 8,
            }
            if internal:
                main.OUTPUTS[output_id]["internal"] = True

    monkeypatch.setattr(main, "_mint", fake_mint)
    return TestClient(main.app)


def _register(n: int = 50) -> None:
    """Remet un registre propre : les tests ne doivent pas dépendre de l'ordre."""
    main.OUTPUTS.clear()
    for index in range(n):
        output_id = f"test_{index}"
        main.OUTPUTS[output_id] = {
            "id": output_id,
            "age_days": round(0.5 + index * 0.35, 3),
            "one_time_public_key_hex": "aa" * 32,
            "tx_public_key_hex": "bb" * 32,
            "encrypted_amount_hex": "cc" * 8,
        }
    main.spent_key_images.clear()
    main.spent_output_ids.clear()


def test_decoys_biaises_vers_les_recents(client):
    _register(200)
    drawn = [
        age
        for _ in range(40)
        for age in client.get("/decoys", params={"count": 10}).json()["ages_days"]
    ]
    median = statistics.median(drawn)
    # Tirage uniforme : médiane ≈ âge médian du registre (≈ 35 j ici).
    # Tirage gamma biaisé récents : médiane très inférieure.
    assert median < 10.0, f"médiane {median:.1f} j : le tirage n'est pas biaisé récents"


def test_decoys_ne_contiennent_jamais_des_outputs_internes(client):
    _register(50)
    main.OUTPUTS["probe_x"] = {
        "id": "probe_x",
        "age_days": 0.01,
        "internal": True,
        "one_time_public_key_hex": "aa" * 32,
        "tx_public_key_hex": "bb" * 32,
        "encrypted_amount_hex": "cc" * 8,
    }
    for _ in range(20):
        assert "probe_x" not in client.get("/decoys", params={"count": 10}).json()["decoys"]


def test_outputs_unspent_exclut_les_outputs_depenses(client):
    _register(300)
    spent = "test_3"
    main.spent_output_ids.add(spent)
    ids = {
        o["id"]
        for o in client.get("/outputs", params={"unspent": True}).json()["outputs"]
    }
    assert spent not in ids


def test_le_pool_se_realimente_pour_enchainer_les_demos(client):
    """Deux passages de la démo ne doivent pas épuiser le registre."""
    _register(30)
    for output_id in list(main.OUTPUTS):
        main.spent_output_ids.add(output_id)
    body = client.get("/outputs", params={"unspent": True}).json()
    assert body["count"] >= 250, "le registre ne s'est pas réalimenté"


def test_quarantaine_retire_les_plus_anciens(client):
    _register(100)
    oldest = {
        o["id"]
        for o in sorted(main.OUTPUTS.values(), key=lambda o: o["age_days"], reverse=True)[:25]
    }
    response = client.post("/admin/quarantine-outputs")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 25
    assert body["criterion"] == "oldest_outputs"
    assert not (oldest & set(main.OUTPUTS)), "un output ancien a survécu à la quarantaine"


def test_ring_size_modifie_la_taille_par_defaut(client):
    _register(100)
    before = main.RING_SIZE
    try:
        assert client.post("/admin/ring-size", json={"ring_size": 25}).status_code == 200
        assert len(client.get("/decoys").json()["decoys"]) == 24
        assert client.post("/admin/ring-size", json={"ring_size": 1}).status_code == 400
    finally:
        main.RING_SIZE = before


def test_pin_refuse_compte_et_refuse(client):
    main.pin_attempts.clear()
    main.pin_failures.clear()
    assert client.post("/pin/verify", json={"pin": "0000"}).status_code == 401
    stats = client.get("/pin/stats").json()
    assert stats["attempts"] == 1 and stats["failures"] == 1
    assert stats["failure_ratio"] == 1.0
    assert client.post("/pin/verify", json={"pin": main.PIN_CODE}).status_code == 200


def test_output_inconnu_refuse(client):
    _register(10)
    response = client.post(
        "/tx/submit",
        json={"real_output_id": "inconnu", "amount": 1},
    )
    assert response.status_code == 400


def test_aucune_cle_privee_exposee(client):
    """Le portefeuille publie ses clés PUBLIQUES, jamais les privées."""
    main.WALLET.update(
        view_private="11" * 32, spend_private="22" * 32,
        view_public="aa" * 32, spend_public="bb" * 32,
    )
    body = client.get("/wallet/public").json()
    assert body["public_view_key_hex"] == "aa" * 32
    serialized = str(body)
    assert "11" * 32 not in serialized and "22" * 32 not in serialized


def test_healthz_expose_l_etat_du_noyau_crypto(client):
    body = client.get("/healthz").json()
    assert body["crypto_core"] in ("ready", "degraded")
    assert "crypto_last_error" in body
