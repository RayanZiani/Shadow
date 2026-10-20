# Installation et tests en local

Ce document décrit l'installation complète du poste de démonstration et la
procédure de recette. Il est écrit pour **macOS (Apple Silicon)** — le poste qui
a servi à valider la chaîne — avec les équivalents Linux et Windows.

> Ce qui a été validé sur ce poste : `docker compose up` complet, noyau Rust
> compilé, `tools/e2e_demo.py` trois fois de suite, 32 tests automatisés.
> Voir §6 pour les chiffres mesurés.

---

## 1. Ce qu'il faut installer

| Outil | Version utilisée | Pourquoi |
|---|---|---|
| Docker + Compose | Docker 29.5 / Compose 5.6 | tout le SI conteneurisé |
| Rust (`rustup`) | 1.99 (stable) | `monero-crypto-core` |
| Python | 3.12 | tests, recette, outils ML |
| Node.js | 26.12 / npm 11.20 | dashboard (`npm run build`) |
| Ansible | dans l'image `remediation-engine` | rien à installer sur l'hôte |

### macOS

```bash
# Docker : Colima + client docker (pas de Docker Desktop, pas de licence)
brew install colima docker docker-compose
colima start --cpu 4 --memory 8 --disk 60
docker version            # doit afficher le serveur 29.x

# Compose est un plugin : il faut le déclarer si `docker compose` est inconnu
mkdir -p ~/.docker && cat > ~/.docker/config.json <<'EOF'
{ "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"] }
EOF

# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"

# Python et Node
brew install python@3.12 node
```

`colima start` alloue 8 Go de RAM à la VM : en dessous de 6 Go, la
construction de l'image `ml-detector` (torch + scikit-learn) échoue par OOM.

### Linux (poste hôte du workshop)

```bash
sudo apt install docker.io docker-compose-plugin build-essential python3.12 python3.12-venv nodejs npm
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
sudo usermod -aG docker "$USER"   # puis se reconnecter
```

### Windows (poste de démonstration visé par le sujet)

- Docker Desktop + WSL2, **16 Go de RAM minimum** (exigence du sujet).
- Rust : `winget install Rustlang.Rustup`.
- Python 3.12 : `winget install Python.Python.3.12`.
- Node : `winget install OpenJS.NodeJS.LTS`.
- Le pont hardware tourne **sur l'hôte**, jamais dans un conteneur
  (`hardware/bridge/hardware_bridge.py`, cf. `docs/hardware/CONCEPT-PHYSIQUE.md`).

---

## 2. Premier lancement

```bash
cd Shadow
cp .env.example .env          # valeurs de démonstration, aucun secret réel

docker compose build          # ~6-8 min la première fois (torch, cargo)
docker compose up -d
docker compose ps             # tout doit être "Up" / "healthy"
```

### Vérification en 20 secondes

```bash
curl -s localhost:8083/healthz   # model: ready, last_webhook_error: null
curl -s localhost:8084/healthz   # state: NORMAL_TX
curl -s localhost:8080/healthz   # outputs > 250, ring_size: 11
curl -s localhost:8081/healthz   # implemented / placeholder (voir §7)
curl -s localhost:8082/healthz   # jwt_key_source: fallback_env ou vault
open http://localhost:5173       # dashboard
```

Le dashboard doit afficher **SSE OK**, un score ML sous le seuil, et
« TX DE PREUVE : OK ».

### Vault (optionnel mais recommandé)

Sans token, `auth-service` signe avec une clé de dev et l'annonce
(`jwt_key_source: fallback_env`). Pour activer la vraie lecture :

```bash
TOKEN=$(docker compose logs vault | grep -o 'hvs\.[A-Za-z0-9]*' | head -1)
KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
docker compose exec -T -e VAULT_TOKEN="$TOKEN" vault vault kv put secret/jwt signing_key="$KEY"
printf 'VAULT_TOKEN=%s\n' "$TOKEN" >> .env
docker compose up -d auth-service remediation-engine
curl -s localhost:8082/healthz     # jwt_key_source: vault
```

À partir de là, l'étape 3 du playbook de remédiation **fait réellement tourner**
la clé de signature, et `auth-service` la relit au bout de 30 s (cache).

---

## 3. Tests automatisés

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu

./tools/run_tests.sh          # tout, y compris cargo test
./tools/run_tests.sh --fast   # sans la vérification des artefacts ML
```

| Suite | Fichier | Ce qu'elle prouve |
|---|---|---|
| remediation-engine | `services/remediation-engine/test_state_bus.py` (7) | contrat d'état, SSE, `seq` croissant, abonné lent non bloquant |
| remediation-engine | `services/remediation-engine/test_incident_flow.py` (5) | ordre `UNDER_ATTACK → REMEDIATING → RECOVERED_TX`, verrou, échec explicite |
| ml-detector | `services/ml-detector/test_detector.py` (11) | invariance au volume, médiane des intervalles, sonde exclue, purge de fenêtre, anti-rebond, réarmement |
| payment-api | `services/payment-api/test_payment_api.py` (8) | leurres gamma, `unspent`, quarantaine des plus anciens, `ring-size`, PIN |
| hardware | `hardware/bridge/test_hardware_bridge.py` (9) | trame série, états inconnus, mode `--rich`, pont strictement en lecture |
| monero-crypto-core | `services/monero-crypto-core/src/main.rs` (4) | décodage de point, refus des entrées invalides (400, pas de panic) |
| shadow-api | `services/shadow-api/test_shadow_api.py` (4) | honeypot : jamais d'erreur visible, journal borné |
| auth-service | `services/auth-service/test_auth_service.py` (5) | clé lue dans Vault, 503 si Vault muet, cache et rotation |

Soit **53 tests**. `cargo test` est lancé par `run_tests.sh` si `cargo` est
présent.

### Tests manuels à faire avant une soutenance

| # | Geste | Attendu |
|---|---|---|
| 1 | `python tools/e2e_demo.py` | `chain_complete: true`, `recovery_seconds < 90` |
| 2 | relancer le même script 2 fois de suite | même résultat : pas de contamination de fenêtre |
| 3 | `python services/ml-detector/validate_real.py --n 20` | `false_trigger_rate: 0.0` |
| 4 | `python hardware/bridge/hardware_bridge.py --dry-run` | trames `STATE:` dans la console |
| 5 | `python hardware/bridge/hardware_bridge.py --replay` | séquence complète en boucle |
| 6 | `cd front-dashboard && npm run build` | build sans erreur |
| 7 | couper `ml-detector` pendant un paiement | le paiement passe quand même (le ML n'est pas dans le chemin critique) |
| 8 | `docker compose restart remediation-engine` | le dashboard se resynchronise (`seq` repris) |

---

## 4. Recalibrer le modèle sur le poste de démonstration

**À refaire sur le PC hôte avant la soutenance** : un seuil calibré sur un autre
poste peut produire des faux positifs.

```bash
cd services/ml-detector
../../.venv/bin/python capture_baseline.py --seconds 420 --out baseline.jsonl
../../.venv/bin/python train.py --from-capture baseline.jsonl --epochs 600
../../.venv/bin/python analyze_separation.py      # AUC par feature
cd ../.. && docker compose build ml-detector && docker compose up -d ml-detector
```

La capture dure 7 minutes et **varie la cadence et la taille d'anneau par blocs
de 20 s** (0,05–2,0 s ; 11 et 25 leurres). C'est volontaire : une baseline
capturée à cadence unique rend le modèle intolérant à toute autre cadence —
mesuré en recette, un trafic parfaitement légitime sortait à +5 σ.

---

## 5. Dépannage

| Symptôme | Cause probable | Correctif |
|---|---|---|
| `docker compose` : `unknown command` | plugin Compose non déclaré | `cliPluginsExtraDirs` (§1) |
| build `ml-detector` tué (OOM) | VM Colima trop petite | `colima stop && colima start --memory 8` |
| `model: degraded` dans `/healthz` | artefacts ML absents de l'image | `docker compose build ml-detector` (le Dockerfile réentraîne si un artefact manque) |
| `last_webhook_error` non nul | `remediation-engine` arrêté | `docker compose up -d remediation-engine` |
| Score au repos > seuil | modèle calibré ailleurs / ring size durci | recapturer (§4) |
| `REMEDIATION_FAILED` avec `rc=127` | Ansible absent de l'image | `docker compose build remediation-engine` |
| Dashboard vide | CORS ou service arrêté | `curl localhost:8084/state`, vérifier `SSE KO` |
| 409 en série sur `/tx/submit` | registre épuisé | redémarrer `payment-api` (le pool se réalimente aussi automatiquement) |

---

## 6. Résultats mesurés sur ce poste (macOS, M3 Pro, 36 Go)

```
Docker Compose        13 services, tous Up
Chaîne P0             3 exécutions consécutives : 5,8 s / 6,6 s / 6,7 s
                      (limite 90 s, marge ≈ 83 s)
Modèle                seuil appris 3,33 ; FPR fenêtre 0,0 % ; rappel 100 %
                      (1 058 fenêtres normales, 1 058 fenêtres d'attaque)
Tests                 53 verts (12 + 11 + 8 + 4 + 5 + 9 + 4)
cargo build --release 12 s
npm run build         148 kB de JS, 426 ms
```

---

## 7. Ce qui n'est pas encore branché (à dire en Q&A, pas à cacher)

| Élément | État | Effet |
|---|---|---|
| `monero-crypto-core` : `ring-sign`, `ring-verify`, `audit-view` | **placeholders** | l'anti-double-dépense et la télémétrie sont réels, la cryptographie ne l'est pas |
| AppRole Vault | non provisionné | le playbook utilise `VAULT_TOKEN` (token de service) |
| WAF | service commenté dans `docker-compose.yml` | le honeypot existe, rien ne lui redirige le trafic |
| `shadow-api` | honeypot fonctionnel, non alimenté | aucun point d'entrée ne l'appelle |
| RAG Ollama | non commencé | pilier IA/DATA partiel |
| Dossier EBIOS / PCA-PRA | non commencé | axe gouvernance (10 pts nationaux) |
| Modèle 3D | non imprimé | v1 paramétrique, cotes à mesurer |
