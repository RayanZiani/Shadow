"""Tests du détecteur ML — les propriétés qui décident de la note en démo.

    python -m pytest test_detector.py -v

Ce que ces tests protègent (chaque point correspond à une panne réellement
rencontrée en recette, cf. docs/COMPTE-RENDU.md §4) :

  * les features sont invariantes au VOLUME de trafic (pas de dérive quand la
    cadence change) ;
  * un trou de 6 s ne produit pas d'anomalie (`inter_arrival` en médiane) ;
  * la sonde `synthetic` n'entre jamais dans la fenêtre ;
  * la fenêtre se purge même sans trafic client (sinon deux démos consécutives
    se contaminent) ;
  * l'anti-rebond exige bien 3 fenêtres consécutives ;
  * le verrou `_incident_active` se réarme (sinon la 2ᵉ attaque est muette).
"""
import time

import numpy as np
import pytest

import detector
from features import FEATURE_NAMES, N_FEATURES, extract_features


def _event(age_days: float = 3.5, ts: float | None = None, **extra) -> dict:
    return {
        "timestamp": time.time() if ts is None else ts,
        "ring_size": 11,
        "ring_verify_cpu_us": 1100.0,
        "decoy_ages_days": [age_days] * 10,
        "key_image_rejected": False,
        "pin_failure": False,
        "tx_latency_ms": 12.0,
        **extra,
    }


def test_vecteur_de_features_bien_forme():
    features = extract_features([_event() for _ in range(10)])
    assert features.shape == (N_FEATURES,)
    assert np.all(np.isfinite(features))


def test_age_median_des_leurres_est_la_feature_porteuse():
    normal = extract_features([_event(age_days=3.5) for _ in range(10)])
    poisoned = extract_features([_event(age_days=31.0) for _ in range(10)])
    index = FEATURE_NAMES.index("decoy_median_age_days")
    assert normal[index] == pytest.approx(3.5, abs=0.01)
    assert poisoned[index] == pytest.approx(31.0, abs=0.01)


def test_features_invariantes_au_volume_de_trafic():
    """Même distribution d'âges, cadence ×4 : la mesure ne doit pas bouger.

    C'est le défaut historique : l'entropie poolée passait de 3,0 à 5,4 bits
    avec 4× plus d'événements, à distribution identique.
    """
    slow = extract_features([_event(ts=time.time() - i * 1.0) for i in range(10)])
    fast = extract_features([_event(ts=time.time() - i * 0.25) for i in range(40)])
    index = FEATURE_NAMES.index("decoy_median_age_days")
    assert slow[index] == pytest.approx(fast[index], abs=1e-9)


def test_un_trou_de_6s_n_est_pas_une_anomalie():
    """La médiane des intervalles ignore une pause ; l'écart-type, non."""
    timestamps = [time.time() - 20, time.time() - 19, time.time() - 18]
    timestamps += [time.time() - 2, time.time() - 1, time.time()]
    events = [_event(ts=t) for t in timestamps]
    vector = extract_features(events)
    assert vector[FEATURE_NAMES.index("inter_arrival_median")] < 2.0


def test_rafale_automatisee_abaisse_l_intervalle_median():
    burst = extract_features([_event(ts=time.time() - i * 0.05) for i in range(12)])
    human = extract_features([_event(ts=time.time() - i * 0.8) for i in range(12)])
    index = FEATURE_NAMES.index("inter_arrival_median")
    assert burst[index] < 0.1 < human[index]


def test_valeurs_aberrantes_bornees():
    """NaN, négatifs et infinis ne doivent jamais produire un score absurde."""
    events = [_event(age_days=float("nan")), _event(age_days=-5.0), _event(age_days=1e9)]
    vector = extract_features(events)
    assert np.all(np.isfinite(vector))
    assert vector[FEATURE_NAMES.index("decoy_median_age_days")] >= 0.0


@pytest.fixture()
def clean_window():
    detector.event_window.clear()
    detector._anomaly_streak = 0
    detector._incident_active = False
    detector._last_trigger = 0.0
    yield
    detector.event_window.clear()


def test_la_sonde_synthetic_n_entre_jamais_dans_la_fenetre(clean_window):
    import asyncio

    asyncio.run(detector.ingest(detector.TelemetryEvent(**_event(synthetic=True))))
    assert len(detector.event_window) == 0


def test_la_fenetre_se_purge_meme_sans_trafic_client(clean_window):
    """Deux `e2e_demo.py` d'affilée ne doivent pas se contaminer."""
    import asyncio

    # Un événement laissé en fenêtre, puis un événement de sonde SEUL : c'est la
    # sonde qui doit purger. Sans cela, la fenêtre ne se vidait qu'à l'arrivée
    # d'un vrai client, donc après l'attaque suivante.
    detector.event_window.append(
        {"timestamp": time.time() - 30, "decoy_ages_days": [31.0] * 10}
    )
    asyncio.run(detector.ingest(detector.TelemetryEvent(**_event(synthetic=True))))
    assert len(detector.event_window) == 0

    # Et un événement périmé n'entre pas non plus dans la fenêtre.
    asyncio.run(
        detector.ingest(detector.TelemetryEvent(**_event(ts=time.time() - 30, age_days=31.0)))
    )
    assert len(detector.event_window) == 0


def test_anti_rebond_trois_fenetres_consecutives(clean_window, monkeypatch):
    """Une seule fenêtre anormale ne déclenche rien ; trois oui."""
    import asyncio

    calls: list[float] = []

    async def fake_trigger(score, features, z_iso, z_ae):
        calls.append(score)

    monkeypatch.setattr(detector, "_trigger_remediation", fake_trigger)
    monkeypatch.setattr(detector, "_anomaly_score", lambda features: (99.0, 5.0, 5.0))
    monkeypatch.setattr(detector, "THRESHOLD", {"threshold": 1.0})

    def send(n: int) -> None:
        for _ in range(n):
            asyncio.run(detector.ingest(detector.TelemetryEvent(**_event())))

    # La 1re évaluation a lieu au 8ᵉ événement (MIN_EVENTS) : streak = 1.
    send(8)
    assert detector._anomaly_streak == 1
    assert calls == [], "déclenchement sur une seule fenêtre anormale"

    # On passe à 2, puis 3 fenêtres consécutives : un seul déclenchement.
    send(1)
    assert calls == []
    send(1)
    assert len(calls) == 1, f"déclenchements inattendus : {len(calls)}"


def test_incident_actif_se_rearme_apres_le_cooldown(clean_window, monkeypatch):
    """La deuxième attaque doit pouvoir déclencher (panne silencieuse connue)."""
    import asyncio

    calls: list[float] = []

    async def fake_trigger(score, features, z_iso, z_ae):
        calls.append(score)

    monkeypatch.setattr(detector, "_trigger_remediation", fake_trigger)
    monkeypatch.setattr(detector, "_anomaly_score", lambda features: (99.0, 5.0, 5.0))
    monkeypatch.setattr(detector, "THRESHOLD", {"threshold": 1.0})
    monkeypatch.setattr(detector, "COOLDOWN_SECONDS", 0.0)

    # MIN_EVENTS (8) : la première évaluation n'a lieu qu'au 8ᵉ événement.
    # Il en faut donc au moins 10 pour obtenir 3 fenêtres consécutives.
    events = [detector.TelemetryEvent(**_event()) for _ in range(11)]
    for _ in range(2):
        for event in events:
            asyncio.run(detector.ingest(event))
        detector.event_window.clear()
    assert len(calls) == 2, f"la seconde attaque n'a pas déclenché ({len(calls)})"


def test_seuil_jamais_code_en_dur():
    """Exigence du sujet : aucun seuil fixe dans le code de production."""
    import inspect

    source = inspect.getsource(detector)
    assert 'THRESHOLD["threshold"]' in source
    assert detector.MODEL_STATE in ("ready", "degraded")


def test_ingest_exige_le_jeton_de_service(monkeypatch):
    """F6 : /ingest ouvert permettait d'ÉVADER la détection.

    En inondant la fenêtre d'observations « normales », on dilue le signal d'une
    attaque jusqu'à passer sous le seuil. L'inverse est possible aussi :
    déclencher le playbook à volonté en forgeant des anomalies.
    Le détecteur observe le SI ; il ne doit pas être une entrée du SI.
    """
    from fastapi.testclient import TestClient

    monkeypatch.delenv("SERVICE_TOKEN", raising=False)
    anonymous = TestClient(detector.app)
    response = anonymous.post("/ingest", json=_event())
    assert response.status_code == 503
    assert "SERVICE_TOKEN" in response.json()["detail"]

    monkeypatch.setenv("SERVICE_TOKEN", "jeton-de-test")
    wrong = TestClient(detector.app, headers={"X-Service-Token": "faux"})
    assert wrong.post("/ingest", json=_event()).status_code == 401

    good = TestClient(detector.app, headers={"X-Service-Token": "jeton-de-test"})
    assert good.post("/ingest", json=_event()).status_code == 200
