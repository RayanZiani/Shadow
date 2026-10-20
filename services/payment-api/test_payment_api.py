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
    # Les endpoints /admin/* exigent le jeton de service (fail-closed sinon).
    monkeypatch.setenv("SERVICE_TOKEN", "jeton-de-test")
    return TestClient(main.app, headers={"X-Service-Token": "jeton-de-test"})


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


def test_anneau_trop_grand_refuse(client):
    """F5 : un anneau non borné saturait le CPU (vérification linéaire en N).

    Un attaquant n'a pas besoin d'une clé : il suffit d'envoyer 100 000 leurres
    pour occuper le noyau. Le plafond est une mesure de disponibilité, pas de
    confidentialité.

    Le test utilise des identifiants **réels** : avec des identifiants inconnus,
    le refus viendrait de la validation des leurres et ne prouverait rien sur
    le plafond.
    """
    _register(100)
    ids = list(main.OUTPUTS)
    response = client.post(
        "/tx/submit",
        json={"real_output_id": ids[0], "decoy_ids": ids[1:70]},
    )
    assert response.status_code == 400
    assert "trop grand" in response.json()["detail"], response.json()

    # Juste sous le plafond, la requête n'est pas refusée pour sa taille :
    # elle passe la validation des leurres (le reste dépend du noyau crypto).
    under = client.post(
        "/tx/submit",
        json={"real_output_id": ids[0], "decoy_ids": ids[1:60]},
    )
    assert "trop grand" not in str(under.json().get("detail", ""))


def test_les_endpoints_admin_sont_fermes_sans_jeton(monkeypatch):
    """F2 : sans SERVICE_TOKEN, l'administration est FERMÉE (503), pas ouverte."""
    monkeypatch.delenv("SERVICE_TOKEN", raising=False)
    _register(10)
    anonymous = TestClient(main.app)
    for path in ("/admin/quarantine-outputs", "/admin/ring-size"):
        response = anonymous.post(path, json={"ring_size": 25})
        assert response.status_code == 503, path
        assert "SERVICE_TOKEN" in response.json()["detail"]


def test_le_pin_par_defaut_ne_fonctionne_plus(client):
    """F4 : le PIN n'est plus une constante du code.

    `TERMINAL_PIN` est généré par `tools/init_env.py` ; l'ancien `1234` est
    refusé sauf s'il a été explicitement configuré (ce que ce test exclut).
    """
    if main.PIN_CODE == "1234":  # pragma: no cover - configuration explicite
        pytest.skip("TERMINAL_PIN vaut explicitement 1234 dans cet environnement")
    assert client.post("/pin/verify", json={"pin": "1234"}).status_code == 401
    assert client.post("/pin/verify", json={"pin": main.PIN_CODE}).status_code == 200


def test_une_double_depense_concurrente_est_refusee(monkeypatch):
    """F8 : deux `/tx/submit` simultanés avec la même key image.

    Avant le correctif, les deux requêtes passaient le test
    `key_image in spent_key_images` (la clé n'était pas encore ajoutée),
    attendaient chacune les appels au noyau, puis répondaient toutes les deux
    `200 confirmed`. L'anti-double-dépense était contournable par deux `curl`
    en parallèle.

    Le test rend le noyau LENT (50 ms) pour ouvrir la fenêtre de course : sans
    le correctif, les deux requêtes se glisseraient dedans.
    """
    import asyncio

    import httpx

    _register(50)
    ids = list(main.OUTPUTS)
    real_id = ids[0]
    decoys = ids[1:11]

    def _shaped(key: str):
        """Valeur plausible selon le nom du champ du contrat crypto."""
        if key == "parts":
            return [{"commitment_hex": "cd" * 32}, {"commitment_hex": "ce" * 32}]
        if key in ("valid", "balanced", "decrypted"):
            return True
        if key in ("amount", "decrypted_amount"):
            return 1
        if key == "verify_cpu_microseconds":
            return 10
        if key.endswith("_hex"):
            return "ab" * 32
        return 1

    class CryptoResponse(dict):
        """Répond à toute clé du contrat, pour ne pas casser à chaque ajout."""

        def __missing__(self, key):
            value = _shaped(key)
            self[key] = value
            return value

    async def fake_crypto(path, payload):
        await asyncio.sleep(0.05)  # ouvre la fenêtre de concurrence
        response = CryptoResponse()
        if path == "/crypto/derive-one-time-key":
            # La clé dérivée doit correspondre à celle annoncée par le
            # registre, sinon `/tx/submit` refuse en 409 avant la partie
            # qu'on veut tester.
            response["one_time_public_key_hex"] = main.OUTPUTS[real_id][
                "one_time_public_key_hex"
            ]
        return response

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "_crypto", fake_crypto)
    monkeypatch.setattr(main, "_send_telemetry", noop)
    monkeypatch.setattr(main, "_report_success_tx", noop)
    monkeypatch.setenv("SERVICE_TOKEN", "jeton-de-test")
    main._pending_key_images.clear()
    main.spent_key_images.clear()
    main.spent_output_ids.clear()

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=30.0
        ) as client:
            payload = {"real_output_id": real_id, "decoy_ids": decoys}
            first, second = await asyncio.gather(
                client.post("/tx/submit", json=payload),
                client.post("/tx/submit", json=payload),
            )
            return sorted([first.status_code, second.status_code])

    codes = asyncio.run(scenario())

    # Exactement une acceptation et un refus : jamais deux 200.
    assert codes == [200, 409], codes
    # Et l'état final est cohérent : la key image est dépensée une seule fois.
    assert "ab" * 32 in main.spent_key_images
    assert real_id in main.spent_output_ids
    assert not main._pending_key_images, main._pending_key_images


def test_le_pin_non_ascii_est_compte_comme_un_echec(client):
    """F11 : `{"pin": "é"}` provoquait un 500 SANS compter l'échec.

    `hmac.compare_digest` sur des `str` lève TypeError dès qu'un caractère est
    non-ASCII. Le PIN n'était donc pas contourné, mais le compteur d'échecs et
    le verrouillage ne voyaient rien : un angle mort.
    """
    main._pin_state.update(failures=0, locked_until=0.0)

    response = client.post("/pin/verify", json={"pin": "é"})
    assert response.status_code == 401, response.status_code
    assert main._pin_state["failures"] == 1, main._pin_state

    # Et un PIN correct reste accepté : on n'a pas cassé la fonctionnalité.
    assert client.post("/pin/verify", json={"pin": main.PIN_CODE}).status_code == 200
    main._pin_state.update(failures=0, locked_until=0.0)
