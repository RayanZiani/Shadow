# Lancement local sans Docker (recette)

> **Mode de secours.** La chaîne est désormais validée sous Docker (13 services,
> Ansible réel, noyau Rust compilé) — voir `docs/INSTALLATION-LOCAL.md`. Ce
> dossier ne sert qu'à un poste où Docker n'est pas installable : il remplace le
> noyau Rust par une cryptographie **factice** et Ansible par un stub.

Ce dossier permet de faire tourner toute la chaîne sur un poste Windows sans
Docker, pour répéter la démonstration. **Ce n'est pas le mode de livraison** :
la livraison, c'est `docker compose up`.

## Ce que ça utilise

- `services/*` en Python (mêmes fichiers qu'en production) ;
- `tools/fake_ansible.py` à la place du vrai `ansible-playbook` ;
- `crypto_shim.py` à la place du noyau Rust (cryptographie factice).

## Prérequis

```bash
py -m venv .venv
.venv\Scripts\pip install fastapi uvicorn httpx numpy scikit-learn joblib pytest
.venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cpu
```

## Lancement

Cinq terminaux, dans l'ordre :

```bash
# 1. shim crypto (port 8081)
.venv\Scripts\python -m uvicorn crypto_shim:app --port 8081

# 2. payment-api (port 8080)
cd services/payment-api
set SELF_URL=http://localhost:8080
set CRYPTO_CORE_URL=http://localhost:8081
set ML_DETECTOR_URL=http://localhost:8083
set REMEDIATION_STATE_URL=http://localhost:8084/state/report
..\..\.venv\Scripts\python -m uvicorn main:app --port 8080

# 3. ml-detector (port 8083)
cd services/ml-detector
set REMEDIATION_WEBHOOK=http://localhost:8084/incident
..\..\.venv\Scripts\python -m uvicorn detector:app --port 8083

# 4. remediation-engine (port 8084)
cd services/remediation-engine
set ANSIBLE_BIN=..\..\.venv\Scripts\python.exe
set ANSIBLE_PLAYBOOK=..\..\tools\fake_ansible.py
..\..\.venv\Scripts\python -m uvicorn main:app --port 8084
```

Puis la répétition générale :

```bash
.venv\Scripts\python tools\e2e_demo.py
```

## Résultat de référence

Mesuré sur ce poste :

```
detection_seconds      10.1
recovery_seconds       12.6
proof_transaction_ok   true
ansible_rc             0
chain_complete         true
```

(Sur Docker, avec Ansible réel : 6,2 à 6,3 s de reprise.)

Si tes chiffres sont très différents, vérifie dans cet ordre :

1. `ml-detector/healthz` → `model` doit valoir `ready` ;
2. `ml-detector/healthz` → `last_webhook_error` doit être `null` ;
3. le score au repos doit être nettement sous le seuil (≈ 0,5 pour un seuil
   à 3,7). Sinon, la baseline d'entraînement ne correspond plus au trafic :
   relance `capture_baseline.py` puis `train.py`.
