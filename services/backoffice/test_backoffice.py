"""Tests du back-office — le parcours d'authentification est le contrat.

    python -m pytest test_backoffice.py -v

Aucun matériel requis : le moteur facial est remplacé par `StubFaceEngine`
(déterministe) et la base est un fichier temporaire. Ce qui est vérifié ici est
exactement ce que la consigne additionnelle impose :

* le mot de passe n'est demandé QUE si le visage est reconnu ;
* un visage inconnu vide l'identifiant (le challenge est invalidé) et éteint
  la caméra (le client reçoit `next: retry`) ;
* deux échecs puis blocage 5 minutes ;
* les LED suivent les états vert clignotant → vert fixe / rouge clignotant ;
* les habilitations sont réellement appliquées (rôle, dernier admin, révocation).
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

# Isolation des imports : le dépôt contient plusieurs services qui exposent
# chacun un `main.py`. Lancé depuis la racine (`pytest services/`), Python
# résoudrait `import main` vers le `main.py` d'un autre service déjà importé et
# les tests échoueraient pour une raison sans rapport avec le code testé.
# `tools/run_tests.sh` lance chaque service depuis son dossier ; ce garde-fou
# rend le lancement global fiable lui aussi.
HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) in sys.path:
    sys.path.remove(str(HERE))
sys.path.insert(0, str(HERE))
for _name in ("main", "config", "db", "faces", "led", "security"):
    _module = sys.modules.get(_name)
    _file = getattr(_module, "__file__", None)
    if _file and pathlib.Path(_file).resolve().parent != HERE:
        del sys.modules[_name]

import main  # noqa: E402  (import volontairement après l'isolation)
from config import Config  # noqa: E402
from db import Database  # noqa: E402


def face(name: str) -> str:
    """Image « visage » pour le moteur stub : deux noms = deux visages."""
    return base64.b64encode(json.dumps({"face": name}).encode()).decode()


def no_face() -> str:
    return base64.b64encode(json.dumps({"no_face": True}).encode()).decode()


@pytest.fixture()
def app(tmp_path):
    cfg = Config(
        db_path=str(tmp_path / "test.db"),
        bootstrap_token="jeton-bootstrap",
        face_engine="stub",
        pbkdf2_iterations=1000,  # tests rapides : la valeur réelle est testée à part
        led_token="jeton-led",
        max_attempts=2,
        lockout_seconds=300,
        session_ttl_seconds=3600,
    )
    application = main.create_app(cfg)
    yield application
    application.state.db.close()


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin(client):
    response = client.post(
        "/auth/bootstrap",
        json={
            "username": "alice",
            "password": "mot-de-passe-alice",
            "token": "jeton-bootstrap",
            "image_b64": face("alice"),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


def login(client, username="alice", image=None, password="mot-de-passe-alice"):
    started = client.post("/auth/identify/start", json={"username": username})
    if started.status_code != 200:
        return started
    challenge = started.json()["challenge_id"]
    identified = client.post(
        "/auth/identify",
        json={"challenge_id": challenge, "image_b64": image or face(username)},
    )
    if identified.status_code != 200 or identified.json().get("recognized") is False:
        return identified
    return client.post(
        "/auth/password", json={"challenge_id": challenge, "password": password}
    )


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def test_aucun_admin_avant_bootstrap(client):
    assert client.get("/healthz").json()["bootstrap_required"] is True


def test_bootstrap_exige_le_jeton(client):
    response = client.post(
        "/auth/bootstrap",
        json={
            "username": "alice",
            "password": "mot-de-passe-alice",
            "token": "mauvais",
            "image_b64": face("alice"),
        },
    )
    assert response.status_code == 403


def test_bootstrap_une_seule_fois(client, admin):
    response = client.post(
        "/auth/bootstrap",
        json={
            "username": "bob",
            "password": "mot-de-passe-bob",
            "token": "jeton-bootstrap",
            "image_b64": face("bob"),
        },
    )
    assert response.status_code == 409
    assert client.get("/healthz").json()["bootstrap_required"] is False


def test_le_mot_de_passe_est_stocke_hache(app, admin):
    row = app.state.db.one("SELECT password_hash FROM users WHERE username='alice'")
    assert row["password_hash"].startswith("pbkdf2_sha256$")
    assert "mot-de-passe-alice" not in row["password_hash"]


# ---------------------------------------------------------------------------
# Parcours nominal
# ---------------------------------------------------------------------------
def test_visage_reconnu_puis_mot_de_passe_ouvre_la_session(client, admin):
    response = login(client)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert response.json()["user"]["role"] == "admin"

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "alice"


def test_la_led_passe_au_vert_clignotant_puis_fixe(client, admin):
    started = client.post("/auth/identify/start", json={"username": "alice"}).json()
    client.post(
        "/auth/identify",
        json={"challenge_id": started["challenge_id"], "image_b64": face("alice")},
    )
    assert client.get("/healthz").json()["led"]["state"] == "green_blink"

    client.post(
        "/auth/password",
        json={"challenge_id": started["challenge_id"], "password": "mot-de-passe-alice"},
    )
    led_state = client.get("/healthz").json()["led"]
    assert led_state["state"] == "green_solid"
    assert led_state["seconds"] == 30


def test_le_mot_de_passe_nest_pas_accepte_sans_visage(client, admin):
    started = client.post("/auth/identify/start", json={"username": "alice"}).json()
    response = client.post(
        "/auth/password",
        json={"challenge_id": started["challenge_id"], "password": "mot-de-passe-alice"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "face_required"


# ---------------------------------------------------------------------------
# Visage refusé
# ---------------------------------------------------------------------------
def test_visage_inconnu_vide_lidentifiant_et_eteint_la_camera(client, admin):
    response = login(client, image=face("mallory"))
    assert response.status_code == 200
    body = response.json()
    assert body["recognized"] is False
    assert body["next"] == "retry"
    assert body["attempts_left"] == 1
    assert body["reason"] == "not_recognized"
    led_state = client.get("/healthz").json()["led"]
    assert led_state["state"] == "red_solid"
    # La consigne impose 30 s de rouge fixe avant l'extinction de la caméra.
    assert led_state["seconds"] == 30

    # Le challenge est consommé : rejouer le mot de passe est impossible.
    challenge = client.post(
        "/auth/identify/start", json={"username": "alice"}
    ).json()["challenge_id"]
    replay = client.post(
        "/auth/password", json={"challenge_id": challenge, "password": "mot-de-passe-alice"}
    )
    assert replay.status_code == 400


def test_absence_de_visage_refusee(client, admin):
    response = login(client, image=no_face())
    assert response.status_code == 200
    body = response.json()
    assert body["reason"] == "no_face"
    # Une image vide est un problème de cadrage, pas une tentative d'usurpation :
    # elle ne consomme pas l'essai (aucun gain pour un attaquant).
    assert body["counted"] is False
    assert body["attempts_left"] == 2


def test_identifiant_inconnu_repond_comme_un_visage_inconnu(client, admin):
    response = login(client, username="inconnu", image=face("inconnu"))
    assert response.status_code == 200
    assert response.json()["reason"] == "not_recognized"


# ---------------------------------------------------------------------------
# Blocage
# ---------------------------------------------------------------------------
def test_deux_echecs_puis_blocage_cinq_minutes(client, admin):
    assert login(client, image=face("mallory")).json()["attempts_left"] == 1
    assert login(client, image=face("mallory")).status_code == 429

    blocked = client.post("/auth/identify/start", json={"username": "alice"})
    assert blocked.status_code == 429
    detail = blocked.json()["detail"]
    assert detail["locked"] is True
    assert 290 <= detail["retry_after"] <= 300
    assert blocked.headers["Retry-After"]


def test_mot_de_passe_invalide_deux_fois_bloque(client, admin):
    def wrong_password():
        started = client.post("/auth/identify/start", json={"username": "alice"})
        if started.status_code != 200:
            return started
        challenge = started.json()["challenge_id"]
        client.post(
            "/auth/identify",
            json={"challenge_id": challenge, "image_b64": face("alice")},
        )
        return client.post(
            "/auth/password",
            json={"challenge_id": challenge, "password": "mauvais-mot-de-passe"},
        )

    first = wrong_password()
    assert first.status_code == 401
    assert first.json()["detail"]["attempts_left"] == 1
    assert client.get("/healthz").json()["led"]["state"] == "red_blink"
    assert client.get("/healthz").json()["led"]["blinks"] == 10

    second = wrong_password()
    assert second.status_code == 429
    assert second.json()["detail"]["locked"] is True


def test_un_admin_peut_debloquer(client, admin):
    # Un second administrateur, créé et enrôlé avant le blocage.
    session = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {session}"}
    created = client.post(
        "/admin/users",
        json={"username": "bob", "password": "mot-de-passe-bob", "role": "admin"},
        headers=headers,
    ).json()["user"]
    client.post(
        f"/admin/users/{created['id']}/face",
        json={"image_b64": face("bob")},
        headers=headers,
    )

    # Alice se bloque.
    assert login(client, image=face("mallory")).json()["attempts_left"] == 1
    assert login(client, image=face("mallory")).status_code == 429

    # Bob se connecte et la débloque.
    bob_session = login(
        client, username="bob", image=face("bob"), password="mot-de-passe-bob"
    ).json()["access_token"]
    bob_headers = {"Authorization": f"Bearer {bob_session}"}
    unlocked = client.post(
        f"/admin/users/{admin['id']}/unlock", headers=bob_headers
    )
    assert unlocked.status_code == 200
    assert unlocked.json()["unlocked"] is True
    assert login(client).status_code == 200


# ---------------------------------------------------------------------------
# Habilitations
# ---------------------------------------------------------------------------
def test_creation_utilisateur_et_role_applique(client, admin):
    session = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {session}"}

    created = client.post(
        "/admin/users",
        json={"username": "bob", "password": "mot-de-passe-bob", "role": "auditor"},
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["user"]["face_enrolled"] is False

    # Sans visage enrôlé, bob ne peut pas se connecter.
    bob = login(client, username="bob", image=face("bob"), password="mot-de-passe-bob")
    assert bob.status_code == 200
    assert bob.json()["reason"] == "not_recognized"

    # Enrôlement du visage par l'administrateur.
    user_id = created.json()["user"]["id"]
    enrolled = client.post(
        f"/admin/users/{user_id}/face",
        json={"image_b64": face("bob")},
        headers=headers,
    )
    assert enrolled.status_code == 200
    assert enrolled.json()["user"]["face_enrolled"] is True

    bob_session = login(
        client, username="bob", image=face("bob"), password="mot-de-passe-bob"
    )
    assert bob_session.status_code == 200
    bob_headers = {"Authorization": f"Bearer {bob_session.json()['access_token']}"}

    # Un auditeur ne peut pas administrer.
    forbidden = client.get("/admin/users", headers=bob_headers)
    assert forbidden.status_code == 403


def test_le_dernier_admin_ne_peut_pas_etre_desactive(client, admin):
    session = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {session}"}
    response = client.delete(f"/admin/users/{admin['id']}", headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "last_admin"


def test_la_deconnexion_revoque_la_session(client, admin):
    session = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {session}"}
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_changer_le_mot_de_passe_ferme_les_sessions(client, admin):
    session = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {session}"}
    client.patch(
        f"/admin/users/{admin['id']}",
        json={"password": "nouveau-mot-de-passe"},
        headers=headers,
    )
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_le_journal_audit_trace_les_evenements(client, admin):
    session = login(client).json()["access_token"]
    events = client.get(
        "/admin/audit", headers={"Authorization": f"Bearer {session}"}
    ).json()["events"]
    names = {event["event"] for event in events}
    assert {"bootstrap", "identify_start", "face_ok", "password_ok"} <= names
    assert "mot-de-passe-alice" not in json.dumps(events)


# ---------------------------------------------------------------------------
# Bandeau LED
# ---------------------------------------------------------------------------
def test_la_led_exige_le_jeton(client, admin):
    assert client.get("/led/state").status_code == 401
    state = client.get("/led/state", params={"token": "jeton-led"}).json()
    assert state["enabled"] is True
    assert state["state"] in {"off", "green_solid"}


def test_les_etats_led_invalides_sont_refuses(app):
    with pytest.raises(ValueError):
        app.state.led.set_state("arc_en_ciel")


def test_un_etat_expire_retombe_a_off(app):
    """Un état ancien ne doit jamais être rejoué tel quel par la carte.

    Si l'ESP8266 redémarre au milieu d'une séquence, il relit `/led/state`. Un
    vert vieux de deux minutes ne doit pas se rallumer : le service expire donc
    ses propres états, en plus de la minuterie du firmware.
    """
    led = app.state.led
    led.set_state("green_solid", seconds=30)
    assert led.state == "green_solid"
    assert led.snapshot("test", "jeton-led")["remaining"] > 25

    # On force l'expiration sans attendre 30 s.
    led._expires_at = 1.0
    snapshot = led.snapshot("test", "jeton-led")
    assert snapshot["state"] == "off"
    assert snapshot["remaining"] == 0


def test_le_rouge_clignotant_a_une_duree_bornee(app):
    app.state.led.set_state("red_blink", blinks=10)
    snapshot = app.state.led.snapshot("test", "jeton-led")
    assert snapshot["blinks"] == 10
    # 10 flashs à 180 ms de demi-période ≈ 4 s, avec une marge d'un cycle.
    assert 3.0 <= snapshot["remaining"] <= 6.0


def test_metriques_prometheus(client, admin):
    body = client.get("/metrics").text
    assert "bo_face_engine_ready 1" in body
    assert "bo_bootstrap_required" in body
    assert 'bo_auth_attempts_total{result="password_ok"}' in body


def test_healthz_signale_le_moteur_stub(client, admin):
    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["face_engine"] == "stub"
    assert health["demo_only"] is True
    assert health["liveness"] == "not_implemented"


# ---------------------------------------------------------------------------
# Moteur réel (OpenCV) — ignoré si les modèles ne sont pas téléchargés
# ---------------------------------------------------------------------------
def test_le_moteur_reel_charge_et_refuse_une_image_sans_visage():
    """Le moteur de démonstration doit charger SANS réseau.

    Les modèles sont téléchargés à la construction de l'image, pas au premier
    scan : ce test vérifie que `build_engine` échoue franchement (et non
    silencieusement) si les fichiers manquent, et qu'il fonctionne s'ils sont là.
    """
    pytest.importorskip("cv2")
    import base64
    from pathlib import Path

    import cv2
    import numpy as np

    from faces import build_engine

    model_dir = Path(__file__).parent / "models"
    cfg = Config(face_engine="opencv", face_model_dir=str(model_dir))
    engine, error = build_engine(cfg)

    if engine is None:
        # Modèles absents : l'erreur doit être explicite, jamais un stub muet.
        assert error and "modèle absent" in error
        pytest.skip("modèles ONNX absents : python fetch_models.py")

    assert engine.name == "sface"
    blank = np.full((480, 640, 3), 128, dtype=np.uint8)
    encoded, buffer = cv2.imencode(".jpg", blank)
    assert encoded
    result = engine.embed_b64(base64.b64encode(buffer.tobytes()).decode())
    assert result.reason == "no_face"
    assert result.embedding is None
