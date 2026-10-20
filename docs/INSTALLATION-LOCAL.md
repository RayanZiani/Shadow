# Installation et tests en local

Ce document décrit l'installation complète du poste de démonstration et la
procédure de recette. Il est écrit pour **macOS (Apple Silicon)** — le poste qui
a servi à valider la chaîne — avec les équivalents Linux et Windows.

> Ce qui a été validé sur ce poste : `docker compose up` complet (14 services),
> noyau Rust compilé avec bLSAG, `tools/e2e_demo.py` trois fois de suite,
> **79 tests automatisés**, Prometheus et Grafana provisionnés.
> Voir §6 pour les chiffres mesurés. Le back-office de la consigne
> additionnelle ajoute un 15ᵉ service et **25 tests** (§8).

---

## 1. Ce qu'il faut installer

| Outil | Version utilisée | Pourquoi |
|---|---|---|
| Docker + Compose | Docker 29.5 / Compose 5.6 | tout le SI conteneurisé |
| Rust (`rustup`) | 1.99 (stable) | `monero-crypto-core` |
| Python | 3.12 | tests, recette, outils ML |
| Node.js | 26.12 / npm 11.20 | dashboard (`npm run build`) |
| Ansible | dans l'image `remediation-engine` | rien à installer sur l'hôte |
| Prometheus / Grafana | images officielles | rien à installer, configurés par `observability/` |

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
python tools/init_env.py          # génère .env avec des secrets aléatoires
docker compose build          # ~6-8 min la première fois (torch, cargo)
docker compose up -d
docker compose ps             # tout doit être "Up" / "healthy"
```

### Vérification en 20 secondes

```bash
curl -s localhost:8083/healthz   # model: ready, last_webhook_error: null
curl -s localhost:8084/healthz   # state: NORMAL_TX
curl -s localhost:8080/healthz   # crypto_core: ready, outputs > 250, ring_size: 11
curl -s localhost:8081/healthz   # implemented / not_implemented (voir §7)
curl -s localhost:8082/healthz   # jwt_key_source: fallback_env ou vault
curl -s localhost:8085/healthz   # waf, deception: false
open http://localhost:5173       # dashboard
open http://localhost:3001       # Grafana (admin / admin au 1er démarrage)
open http://localhost:9090       # Prometheus
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
| ml-detector | `services/ml-detector/test_detector.py` (12) | invariance au volume, médiane des intervalles, sonde exclue, purge de fenêtre, anti-rebond, réarmement |
| payment-api | `services/payment-api/test_payment_api.py` (13) | leurres gamma, `unspent`, quarantaine des plus anciens, `ring-size`, PIN |
| hardware | `hardware/bridge/test_hardware_bridge.py` (9) | trame série, états inconnus, mode `--rich`, pont strictement en lecture |
| monero-crypto-core | `services/monero-crypto-core/src/main.rs` (9) | décodage de point, Pedersen homomorphe, chiffrement réversible, signature d'anneau valide, falsifications rejetées, key image déterministe, dérivation one-time |
| monero-crypto-core | `services/monero-crypto-core/test_crypto_flow.py` (10) | **par HTTP** : émission, dérivation, scan view-only, audit, équilibre des engagements, bLSAG, rejeu |
| waf | `services/waf/test_waf.py` (10) | âge médian des leurres, routage honeypot, **le client légitime n'est jamais dérouté**, reset |
| shadow-api | `services/shadow-api/test_shadow_api.py` (4) | honeypot : jamais d'erreur visible, journal borné |
| auth-service | `services/auth-service/test_auth_service.py` (9) | clé lue dans Vault, 503 si Vault muet, cache et rotation |
| back-office | `services/backoffice/test_backoffice.py` (25) | parcours identifiant → visage → mot de passe, refus, blocage 5 min, LED, habilitations, audit |
| boutique | `services/shop-api/test_shop_api.py` (18) | catalogue en base, panier, achat, stock réservé puis restitué, historique par capability token, administration |
| assistant RAG | `services/rag-assistant/test_rag.py` (12) | index BM25 du dossier, question de référence, citations, mode dégradé extractif |
| ingestion SIEM | `services/remediation-engine/test_alert_ingest.py` (8) | une alerte actionnable ouvre un incident, une alerte de confort non, anti-rebond, jeton |

Soit **151 tests**. Les tests HTTP du noyau se déclarent ignorés si
`docker compose` n'est pas démarré. `cargo test` est lancé par `run_tests.sh` si `cargo` est
présent.

### Tests manuels à faire avant une soutenance

| # | Geste | Attendu |
|---|---|---|
| 1 | `python tools/e2e_demo.py` | `chain_complete: true`, `recovery_seconds < 90`, `attacker_routed_to_honeypot: true` |
| 2 | relancer le même script 2 fois de suite | même résultat : pas de contamination de fenêtre |
| 3 | `python services/ml-detector/validate_real.py --n 20` | `false_trigger_rate: 0.0` |
| 4 | `python hardware/bridge/hardware_bridge.py --dry-run` | trames `STATE:` dans la console |
| 5 | `python hardware/bridge/hardware_bridge.py --replay` | séquence complète en boucle |
| 6 | `cd front-dashboard && npm run build` | build sans erreur |
| 7 | couper `ml-detector` pendant un paiement | le paiement passe quand même (le ML n'est pas dans le chemin critique) |
| 8 | `docker compose restart remediation-engine` | le dashboard se resynchronise (`seq` repris) |
| 9 | `python tools/attack.py all` | poison dérouté, replay 409, burst, malformed 400 sans panic |
| 10 | `python tools/attack.py reset` | `deception: false` |
| 11 | ouvrir `localhost:3001` | dashboard « RESILIENCE-ALPHA — état du service » avec 10 panneaux |
| 12 | `curl -s localhost:9090/api/v1/targets` | 11 cibles, toutes `up` |
| 13 | `python tools/backoffice_check.py` | 0 échec : moteur facial réel, administrateur enrôlé, LED protégée |
| 14 | parcours visage + mot de passe dans le navigateur | vert clignotant → vert fixe 30 s → dashboard |

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
| Grafana refuse le mot de passe | volume `grafana-data` d'un ancien démarrage | `docker compose down -v` (efface les volumes) ou changer via l'UI |
| Cible Prometheus `down` | service sans `/metrics` ou arrêté | `curl localhost:<port>/metrics` |
| Toutes les attaques sont déroutées | déception restée armée | `python tools/attack.py reset` |
| 409 en série sur `/tx/submit` | registre épuisé | redémarrer `payment-api` (le pool se réalimente aussi automatiquement) |
| Back-office : « aucune caméra détectée » | webcam non branchée sur le PC hôte, ou déjà prise par Teams/Zoom | brancher la caméra sur l'hôte, fermer l'autre application, recharger la page |
| Back-office : la caméra est refusée par le navigateur | page ouverte par une **IP LAN** (pas un contexte sécurisé) | ouvrir `http://localhost:5173`, pas `http://192.168.x.x:5173` |
| Back-office : `bootstrap_required: true` | aucun administrateur avec visage | renseigner `BO_BOOTSTRAP_TOKEN`, ouvrir le dashboard, enrôler |
| Back-office : `face_engine: unavailable` | modèles ONNX absents ou illisibles | `docker compose build backoffice` (le build les télécharge et vérifie leur SHA-256) |
| Back-office : `demo_only: true` | `BO_FACE_ENGINE=stub` | remettre `opencv` : le stub ne reconnaît aucun visage réel |
| Back-office : compte bloqué | 2 échecs (visage ou mot de passe) | attendre 5 min ou débloquer depuis l'écran « Habilitations » |
| Bandeau LED éteint en permanence | ESP hors WiFi, ou `LED_DEVICE_TOKEN` différent | `pio device monitor` sur la carte, comparer avec `.env` |

---

## 6. Résultats mesurés sur ce poste (macOS, M3 Pro, 36 Go)

```
Docker Compose        18 services, tous Up
Chaîne P0             3 exécutions consécutives : 5,8 s / 6,6 s / 6,7 s
                      (limite 90 s, marge ≈ 83 s)
Modèle                seuil appris 3,33 ; FPR fenêtre 0,0 % ; rappel 100 %
                      (1 058 fenêtres normales, 1 058 fenêtres d'attaque)
Tests                 79 verts (12 + 11 + 10 + 10 + 10 + 9 + 8 + 4 + 5)
                      + 25 back-office = 104 au total
cargo build --release 2 s (incrémental) / 12 s (à froid)
npm run build         181 kB de JS, 1,1 s
Prometheus            11 cibles up, 20 règles d'alerte évaluées
Grafana               dashboard provisionné, 10 panneaux
Back-office           YuNet + SFace : 29 ms par visage sur CPU, 38 Mo de modèles
```

---

## 7. Ce qui n'est pas encore branché (à dire en Q&A, pas à cacher)

| Élément | État | Effet |
|---|---|---|
| AppRole Vault | non provisionné | le playbook utilise `VAULT_TOKEN` (token de service) |
| Bulletproofs | non implémentées | un montant négatif modulo l passerait la vérification d'équilibre |
| Registre d'outputs | en mémoire | perdu au redémarrage de `payment-api` |
| RAG Ollama | non commencé | pilier IA/DATA couvert par le ML seul |
| Détection de vivacité (visage) | non implémentée | une photo imprimée passerait (`liveness: not_implemented` dans `/healthz`) |
| Bandeau LED ESP8266 | firmware écrit, non flashé | l'authentification fonctionne sans lui |
| Modèle 3D | non imprimé | v1 paramétrique, cotes à mesurer |

---

## 8. Back-office : caméra sur le PC hôte

Exigence de la consigne : la caméra est **obligatoirement** branchée sur le PC
hôte. Conséquence technique : elle est ouverte par le **navigateur du PC hôte**,
via `getUserMedia`, et non par un conteneur.

```
   PC hôte (Windows, point d'accès RESILIENCE-G<n>)
   ┌────────────────────────────────────────────────────────┐
   │  navigateur ──► getUserMedia ──► caméra USB            │
   │      │  JPEG (base64)                                  │
   │      ▼                                                 │
   │  docker : backoffice:8086 ──► YuNet + SFace ──► SQLite │
   │      ▲                                                 │
   │      │ GET /led/state (300 ms, WiFi)                    │
   │  ESP8266 + LED bicolore                                 │
   └────────────────────────────────────────────────────────┘
```

Trois règles :

1. **Ouvrir le dashboard par `http://localhost:5173`**, jamais par une IP du
   réseau : `getUserMedia` exige un « contexte sécurisé » et `localhost` en est
   un, une IP LAN non.
2. **Une seule application peut tenir la caméra.** Fermer Teams / Zoom / une
   autre fenêtre de navigateur qui l'utiliserait, sinon l'erreur `busy` est
   affichée explicitement.
3. **Docker Desktop n'a pas d'accès webcam fiable** (WSL2) : c'est précisément
   pourquoi la capture est côté navigateur, et non dans un conteneur.

Premier démarrage :

```bash
# 1. jeton d'enrôlement (dans .env), puis
docker compose up -d backoffice front-dashboard

# 2. ouvrir http://localhost:5173 → écran d'enrôlement
#    identifiant + mot de passe + jeton + scan du visage

# 3. fermer le bootstrap : retirer BO_BOOTSTRAP_TOKEN de .env
docker compose up -d backoffice

# 4. contrôle
python tools/backoffice_check.py
```

Les modèles faciaux sont téléchargés **à la construction de l'image** et
vérifiés par SHA-256 (`services/backoffice/Dockerfile`) : aucun téléchargement
au runtime, donc aucun échec réseau pendant la démonstration. Pour un poste
sans Docker : `python services/backoffice/fetch_models.py` puis
`uvicorn main:create_app --factory --port 8086`.
