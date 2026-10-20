# Installation et tests en local

Ce document décrit l'installation complète du **poste de démonstration Windows**
— celui que vise le sujet — puis celle du **poste de développement macOS
(Apple Silicon)** qui a servi à valider la chaîne. Les commandes Windows sont en
**PowerShell** ; les équivalents POSIX (macOS/Linux) sont donnés à chaque fois.

> Ce qui a été validé : `docker compose up` complet (**19 services**), noyau
> Rust compilé avec bLSAG, `tools/e2e_demo.py` trois fois de suite,
> **219 tests automatisés**, Prometheus, Alertmanager et Grafana provisionnés,
> boutique en PostgreSQL, assistant RAG et back-office à deux facteurs.
> Voir §7 pour les chiffres mesurés.

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

### Windows (poste de démonstration) — PowerShell

```powershell
winget install Docker.DockerDesktop
winget install Rustlang.Rustup
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
```

- Docker Desktop + **WSL2**, **16 Go de RAM minimum** (exigence du sujet).
  Vérifier après installation : `docker version` doit afficher le serveur 29.x,
  et `docker compose version` le plugin Compose 5.x.
- Rust : fermer puis rouvrir le terminal après `winget` (`cargo --version`).
- Python 3.12 : `python --version` doit répondre 3.12.x ; si la commande ouvre
  le Microsoft Store, désactiver l'alias `python.exe` dans
  *Paramètres → Applications → Alias d'exécution des applications*.
- Node : `node --version` / `npm --version`.
- Le pont hardware tourne **sur l'hôte**, jamais dans un conteneur
  (`hardware/bridge/hardware_bridge.py`, cf. `docs/hardware/CONCEPT-PHYSIQUE.md`).
- Les scripts `.ps1` du dépôt ne sont pas signés. Si PowerShell les refuse :
  `powershell -ExecutionPolicy Bypass -File .\tools\run_tests.ps1 -Fast`
  (ou `Set-ExecutionPolicy -Scope Process Bypass` pour la session courante).

### macOS (poste de développement, Apple Silicon)

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

### Linux

```bash
sudo apt install docker.io docker-compose-plugin build-essential python3.12 python3.12-venv nodejs npm
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
sudo usermod -aG docker "$USER"   # puis se reconnecter
```

---

## 2. Premier lancement

**Windows (PowerShell) :**

```powershell
cd C:\Users\seo\Documents\Work\cours\Shadow
python tools/init_env.py      # génère .env avec des secrets aléatoires
docker compose build          # ~6-8 min la première fois (torch, cargo)
docker compose up -d
docker compose ps             # 19 services, tous "Up" / "healthy"
```

**macOS / Linux :**

```bash
cd Shadow
python3 tools/init_env.py
docker compose build
docker compose up -d
docker compose ps
```

`tools/init_env.py` est obligatoire : il génère `SERVICE_TOKEN`,
`BO_BOOTSTRAP_TOKEN`, `LED_DEVICE_TOKEN` et `TERMINAL_PIN` (jamais `1234`).
Sans jeton de service, les endpoints d'administration sont fermés (503).

### Vérification en 20 secondes

**Windows (PowerShell) :**

```powershell
curl.exe -s localhost:8080/healthz   # crypto_core: ready, outputs > 250, ring_size: 11
curl.exe -s localhost:8081/healthz   # implemented / not_implemented (voir §8)
curl.exe -s localhost:8082/healthz   # jwt_key_source: fallback_env ou vault
curl.exe -s localhost:8083/healthz   # model: ready, last_webhook_error: null
curl.exe -s localhost:8084/healthz   # state: NORMAL_TX
curl.exe -s localhost:8085/healthz   # waf, deception: false
curl.exe -s localhost:8086/healthz   # back-office : face_engine, liveness
curl.exe -s localhost:8087/healthz   # boutique : database: postgresql
curl.exe -s localhost:8091/healthz   # RAG : index.documents, generation.ollama
curl.exe -s localhost:3100/ready     # Loki : ready
start http://localhost:5173          # vitrine + dashboard
start http://localhost:3001          # Grafana (admin / admin au 1er démarrage)
start http://localhost:9090          # Prometheus
```

**macOS / Linux :** mêmes `curl`, remplacer `start` par `open` (macOS) ou
`xdg-open` (Linux).

Le dashboard doit afficher **SSE OK**, un score ML sous le seuil, et
« TX DE PREUVE : OK ». La vitrine doit afficher **6 articles avec leur image**
(`GET /products` → `image_url` non vide pour chacun).

### Vault (optionnel mais recommandé)

**Windows (PowerShell) :**

```powershell
$TOKEN = (docker compose logs vault | Select-String -Pattern 'hvs\.[A-Za-z0-9]+' |
          Select-Object -First 1).Matches.Value
$KEY = python -c "import secrets; print(secrets.token_hex(32))"
docker compose exec -T -e VAULT_TOKEN=$TOKEN vault vault kv put secret/jwt signing_key=$KEY
Add-Content .env "VAULT_TOKEN=$TOKEN"
docker compose up -d auth-service remediation-engine
curl.exe -s localhost:8082/healthz   # jwt_key_source: vault
```

**macOS / Linux :**

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

## 3. Point d'accès RESILIENCE-G<n> sur le PC hôte

La consigne impose que l'ESP8266 soit « connecté en WiFi au point d'accès de
votre solution ». Le point d'accès est **le PC hôte lui-même** : c'est le
partage de connexion de Windows, pas la box du réseau domestique.

### Activer le partage

| Windows 11 | Windows 10 |
|---|---|
| Paramètres → **Réseau et Internet** → **Point d'accès mobile** | Paramètres → **Réseau et Internet** → **Partage de connexion** |

1. **Nom du réseau** : `RESILIENCE-G1` (le même que `WIFI_SSID` dans
   `hardware/led_bandeau_esp8266/src/secrets.h`).
2. **Mot de passe** : WPA2, 8 caractères minimum — jamais le mot de passe du
   WiFi domestique. Le noter sur la fiche, pas à l'écran.
3. **Bande : 2,4 GHz.** L'ESP8266 ne voit pas le 5 GHz ; un point d'accès en
   5 GHz seul se traduit par un bandeau muet et rien d'autre.
4. Activer le partage. Windows crée un réseau `192.168.137.0/24` : le PC hôte
   est la **passerelle `192.168.137.1`**, les clients reçoivent une adresse par
   DHCP à partir de `192.168.137.2`.

### Autoriser le port 8086

Le back-office écoute sur l'hôte (`ports: ["8086:8086"]`). Sans règle entrante,
l'ESP est accepté sur le WiFi mais ses `GET /led/state` sont jetés : la LED
reste éteinte et rien ne le signale côté serveur.

```powershell
New-NetFirewallRule -DisplayName "RESILIENCE back-office 8086 (bandeau LED)" `
  -Direction Inbound -Protocol TCP -LocalPort 8086 -Action Allow -Profile Any
```

`-Profile Any` est volontaire : le réseau créé par le partage de connexion est
souvent classé « public » par Windows, et une règle limitée au profil « privé »
laisserait passer le WiFi mais pas les requêtes de l'ESP. La règle ne concerne
que le port 8086, sur le réseau du point d'accès.

### Vérifier que l'ESP a bien une adresse

```powershell
ipconfig | Select-String -Pattern "Passerelle|Gateway"
# → 192.168.137.1 sur la carte « Microsoft Wi-Fi Direct Virtual Adapter »

arp -a | Select-String -Pattern "192.168.137\."
# → le bail DHCP de l'ESP (ex. 192.168.137.42)
```

Le journal série de la carte donne la même information, et c'est la preuve à
garder pour la recette :

```
[wifi] connexion a RESILIENCE-G1
[wifi] connecte, ip=192.168.137.42
```

Si aucune ligne n'apparaît : vérifier le SSID (2,4 GHz), le mot de passe, puis
`pio device monitor -b 115200`. Le contrat complet est dans
[`hardware/led_bandeau_esp8266/README.md`](../hardware/led_bandeau_esp8266/README.md).

### La contrainte qui surprend en salle

Le **navigateur du PC hôte** doit ouvrir `http://localhost:5173`, **jamais**
`http://192.168.137.1:5173` ni une IP LAN :

- `getUserMedia` (la caméra) exige un « contexte sécurisé » ;
- `localhost` en est un, une IP LAN non — le navigateur refuse la caméra sans
  que le serveur soit en cause ;
- l'ESP, lui, utilise bien `192.168.137.1:8086` : c'est un client du réseau,
  pas le navigateur de l'hôte.

Autrement dit : **deux adresses pour la même machine, deux usages.** Le
navigateur reste en `localhost`, le bandeau passe par la passerelle.

---

## 4. Tests automatisés

**Windows (PowerShell) :**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu

.\tools\run_tests.ps1          # tout, y compris cargo test
.\tools\run_tests.ps1 -Fast    # sans la vérification des artefacts ML
```

**macOS / Linux :**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu

./tools/run_tests.sh          # tout, y compris cargo test
./tools/run_tests.sh --fast   # sans la vérification des artefacts ML
```

Les deux scripts lancent **16 suites Python** (15 suites pytest + le contrôle
d'encodage), puis les tests Rust et les tests HTTP. `run_tests.ps1` est
l'équivalent strict de `run_tests.sh` : mêmes suites, même ordre.

| Suite | Fichier | Ce qu'elle prouve |
|---|---|---|
| remediation-engine | `services/remediation-engine/test_state_bus.py` (12) | contrat d'état, SSE, `seq` croissant, abonné lent non bloquant |
| remediation-engine | `services/remediation-engine/test_incident_flow.py` (**5**, script) | ordre `UNDER_ATTACK → REMEDIATING → RECOVERED_TX`, verrou, échec explicite |
| remediation-engine | `services/remediation-engine/test_incident_flow.py` (2, pytest) | état terminal garanti : une erreur du playbook publie `REMEDIATION_FAILED` |
| remediation-engine | `services/remediation-engine/test_alert_ingest.py` (13) | une alerte actionnable ouvre un incident, une alerte de confort non, anti-rebond, jeton, corrélation |
| ml-detector | `services/ml-detector/test_detector.py` (12) | invariance au volume, médiane des intervalles, sonde exclue, purge de fenêtre, anti-rebond, réarmement |
| payment-api | `services/payment-api/test_payment_api.py` (27) | leurres gamma, `unspent`, quarantaine des plus anciens, `ring-size`, PIN, double dépense concurrente |
| payment-api | `services/payment-api/test_registry.py` (16) | journal PostgreSQL, rechargement au démarrage, repli explicite, écriture groupée |
| hardware | `hardware/bridge/test_hardware_bridge.py` (9) | trame série, états inconnus, mode `--rich`, pont strictement en lecture |
| monero-crypto-core | `services/monero-crypto-core/src/main.rs` (9) | décodage de point, Pedersen homomorphe, chiffrement réversible, signature d'anneau valide, falsifications rejetées, key image déterministe, dérivation one-time |
| monero-crypto-core | `services/monero-crypto-core/test_crypto_flow.py` (10) | **par HTTP** : émission, dérivation, scan view-only, audit, équilibre des engagements, bLSAG, rejeu |
| waf | `services/waf/test_waf.py` (10) | âge médian des leurres, routage honeypot, **le client légitime n'est jamais dérouté**, reset |
| shadow-api | `services/shadow-api/test_shadow_api.py` (4) | honeypot : jamais d'erreur visible, journal borné |
| auth-service | `services/auth-service/test_auth_service.py` (9) | clé lue dans Vault, 503 si Vault muet, cache et rotation |
| back-office | `services/backoffice/test_backoffice.py` (29) | parcours identifiant → visage → mot de passe, refus, blocage 5 min, LED, habilitations, audit, dernier administrateur |
| boutique | `services/shop-api/test_postgres_dialect.py` (4) | aucun SQL ne compare un booléen à un entier (casse PostgreSQL) |
| boutique | `services/shop-api/test_shop_api.py` (32) | catalogue en base, panier, achat, stock réservé puis restitué, historique par capability token, administration, idempotence, `pending` |
| assistant RAG | `services/rag-assistant/test_rag.py` (16) | index BM25 du dossier, question de référence, citations, mode dégradé extractif, disjoncteur |
| encodage | `tools/check_encoding.py` (contrôle) | aucun mojibake dans les fichiers livrés |

Soit **219 tests** : **195 pytest**, **5 tests de script**, **9 tests Rust** et
**10 tests HTTP**. Les tests HTTP du noyau se déclarent ignorés si
`docker compose` n'est pas démarré ; `cargo test` est lancé par les deux
scripts si `cargo` est présent.

### Tests manuels à faire avant une soutenance

| # | Geste | Attendu |
|---|---|---|
| 1 | `python tools/preflight.py` | code de sortie 0, aucune ligne « NON DÉMONTRABLE » |
| 2 | `python tools/e2e_demo.py` | `chain_complete: true`, `recovery_seconds < 90`, `attacker_routed_to_honeypot: true` |
| 3 | relancer le même script 2 fois de suite | même résultat : pas de contamination de fenêtre |
| 4 | `python tools/e2e_shop.py` | achat complet, commande `paid`, stock décrémenté, code 0 |
| 5 | `python services/ml-detector/validate_real.py --n 20` | `false_trigger_rate: 0.0` |
| 6 | `python hardware/bridge/hardware_bridge.py --dry-run` | trames `STATE:` dans la console |
| 7 | `python hardware/bridge/hardware_bridge.py --replay` | séquence complète en boucle |
| 8 | `cd front-dashboard && npm run build` | build sans erreur |
| 9 | couper `ml-detector` pendant un paiement | le paiement passe quand même (le ML n'est pas dans le chemin critique) |
| 10 | `docker compose restart remediation-engine` | le dashboard se resynchronise (`seq` repris) |
| 11 | `python tools/attack.py all` | poison dérouté, replay 409, burst, malformed 400 sans panic |
| 12 | `python tools/attack.py reset` | `deception: false` |
| 13 | `python tools/attack.py shop` | 6 familles refusées, stock jamais négatif |
| 14 | ouvrir `localhost:3001` → **Explore** → source **Loki** | une requête `{service="payment-api"}` remonte des lignes |
| 15 | `curl -s localhost:9090/api/v1/targets` | 12 cibles, toutes `up` |
| 16 | `python tools/backoffice_check.py` | 0 échec : moteur facial réel, administrateur enrôlé, LED protégée |
| 17 | parcours visage + mot de passe dans le navigateur | vert clignotant → vert fixe 30 s → dashboard |
| 18 | `python tools/chaos.py --list` | les 6 scénarios sont listés (aucun conteneur arrêté) |

---

## 5. Recalibrer le modèle sur le poste de démonstration

**À refaire sur le PC hôte avant la soutenance** : un seuil calibré sur un autre
poste peut produire des faux positifs.

**Windows (PowerShell) :**

```powershell
cd services\ml-detector
..\..\.venv\Scripts\python.exe capture_baseline.py --seconds 420 --out baseline.jsonl
..\..\.venv\Scripts\python.exe train.py --from-capture baseline.jsonl --epochs 600
..\..\.venv\Scripts\python.exe analyze_separation.py      # AUC par feature
cd ..\.. ; docker compose build ml-detector ; docker compose up -d ml-detector
```

**macOS / Linux :**

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

## 6. Dépannage

| Symptôme | Cause probable | Correctif |
|---|---|---|
| `docker compose` : `unknown command` | plugin Compose non déclaré (macOS) | `cliPluginsExtraDirs` (§1) |
| `.\tools\run_tests.ps1` refusé par PowerShell | script non signé | `powershell -ExecutionPolicy Bypass -File .\tools\run_tests.ps1 -Fast` |
| build `ml-detector` tué (OOM) | VM Colima trop petite / RAM insuffisante | `colima stop && colima start --memory 8` ; Windows : 16 Go minimum |
| `model: degraded` dans `/healthz` | artefacts ML absents de l'image | `docker compose build ml-detector` (le Dockerfile réentraîne si un artefact manque) |
| `last_webhook_error` non nul | `remediation-engine` arrêté | `docker compose up -d remediation-engine` |
| Score au repos > seuil | modèle calibré ailleurs / ring size durci | recapturer (§5) |
| `REMEDIATION_FAILED` avec `rc=127` | Ansible absent de l'image | `docker compose build remediation-engine` |
| Dashboard vide | CORS ou service arrêté | `curl localhost:8084/state`, vérifier `SSE KO` |
| Grafana refuse le mot de passe | volume `grafana-data` d'un ancien démarrage | `docker compose down -v` (efface les volumes) ou changer via l'UI |
| **Grafana Explore vide** | source Loki non provisionnée | Connections → Data sources → Loki, URL `http://loki:3100`, Save & test |
| Cible Prometheus `down` | service sans `/metrics` ou arrêté | `curl localhost:<port>/metrics` |
| Toutes les attaques sont déroutées | déception restée armée | `python tools/attack.py reset` |
| 409 en série sur `/tx/submit` | registre épuisé | redémarrer `payment-api` (le pool se réalimente aussi automatiquement) |
| Boutique 503 / catalogue vide | PostgreSQL absent | `docker compose up -d db-postgres shop-api` ; `curl localhost:8087/healthz` doit dire `postgresql` |
| Commande bloquée en `pending` | processus mort entre réservation et paiement | attendre le balayage (TTL 120 s, toutes les 30 s) : elle passe en `failed`, stock restitué |
| Assistant RAG muet | index absent ou service arrêté | `docker compose up -d rag-assistant` ; `curl localhost:8091/healthz` |
| Back-office : « aucune caméra détectée » | webcam non branchée sur le PC hôte, ou déjà prise par Teams/Zoom | brancher la caméra sur l'hôte, fermer l'autre application, recharger la page |
| Back-office : la caméra est refusée par le navigateur | page ouverte par une **IP LAN** (pas un contexte sécurisé) | ouvrir `http://localhost:5173`, pas `http://192.168.137.1:5173` (§3) |
| Back-office : `bootstrap_required: true` | aucun administrateur avec visage | renseigner `BO_BOOTSTRAP_TOKEN`, ouvrir le dashboard, enrôler |
| Back-office : `face_engine: unavailable` | modèles ONNX absents ou illisibles | `docker compose build backoffice` (le build les télécharge et vérifie leur SHA-256) |
| Back-office : `demo_only: true` | `BO_FACE_ENGINE=stub` | remettre `opencv` : le stub ne reconnaît aucun visage réel |
| Back-office : compte bloqué | 2 échecs (visage ou mot de passe) | attendre 5 min ou débloquer depuis l'écran « Habilitations » |
| Bandeau LED éteint en permanence | ESP hors WiFi, port 8086 bloqué, ou `LED_DEVICE_TOKEN` différent | `pio device monitor` sur la carte, règle de pare-feu (§3), comparer avec `.env` |
| ESP associé au WiFi mais aucune LED | mauvaise passerelle dans `secrets.h` | `BO_HOST` doit valoir `192.168.137.1`, pas l'IP du réseau domestique |

---

## 7. Résultats mesurés sur la pile de démonstration

```
Docker Compose        19 services, tous Up
Chaîne P0             3 exécutions consécutives : 6,6 s / 7,1 s / 7,2 s
                      (limite 90 s, marge ≈ 83 s)
Modèle                seuil appris 4,6091 ; FPR fenêtre 0,6 % (0,2 % sur
                      3 fenêtres consécutives) ; rappel 100 %
                      (1 129 fenêtres normales)
Tests                 219 verts (195 pytest + 5 script + 9 Rust + 10 HTTP)
cargo build --release 2 s (incrémental) / 12 s (à froid)
npm run build         206,82 kB de JS, 1,1 s
Prometheus            12 cibles up, 24 règles d'alerte évaluées (+ 4 règles de logs Loki)
Grafana               dashboard provisionné, 10 panneaux
Back-office           YuNet + SFace : 29 ms par visage sur CPU, 38 Mo de modèles
```

---

## 8. Ce qui n'est pas encore branché (à dire en Q&A, pas à cacher)

| Élément | État | Effet |
|---|---|---|
| AppRole Vault | non provisionné | le playbook utilise `VAULT_TOKEN` (token de service) |
| Bulletproofs | non implémentées | un montant négatif modulo l passerait la vérification d'équilibre |
| Registre d'outputs | **PostgreSQL** (`registry.py`) | rechargé au démarrage ; repli mémoire explicite sans `DATABASE_URL` |
| RAG Ollama | RAG livré (BM25), Ollama optionnel | mode extractif explicite dans `/healthz` (`generation.ollama`) |
| Détection de vivacité (visage) | non implémentée | une photo imprimée passerait (`liveness: not_implemented` dans `/healthz`) |
| Bandeau LED ESP8266 | firmware écrit et compilé, **à flasher** | l'authentification fonctionne sans lui |
| Modèle 3D | non imprimé | v1 paramétrique, cotes à mesurer |

---

## 9. Back-office : caméra sur le PC hôte

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
   un, une IP LAN non (§3).
2. **Une seule application peut tenir la caméra.** Fermer Teams / Zoom / une
   autre fenêtre de navigateur qui l'utiliserait, sinon l'erreur `busy` est
   affichée explicitement.
3. **Docker Desktop n'a pas d'accès webcam fiable** (WSL2) : c'est précisément
   pourquoi la capture est côté navigateur, et non dans un conteneur.

Premier démarrage :

```powershell
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

---

## 10. Ce qui doit être vrai la veille

À contrôler dans cet ordre, la veille **et** le matin. Le runbook complet est
[`docs/DEMO.md`](DEMO.md) §0 ; cette liste est le minimum vérifiable.

| # | Contrôle | Attendu |
|---|---|---|
| 1 | `python tools/preflight.py` | code 0 : `.env`, PIN transmis, services P0, catalogue, dépôt propre |
| 2 | `.\tools\run_tests.ps1 -Fast` | 16 suites Python vertes (Rust/HTTP si l'hôte les a) |
| 3 | `python tools/e2e_demo.py` **trois fois de suite** | trois `chain_complete: true`, pas de contamination de fenêtre |
| 4 | `python tools/e2e_shop.py` | achat complet, commande `paid`, stock décrémenté |
| 5 | **PIN noté** sur une fiche papier | lu dans `.env` en terminal privé, jamais affiché ni dicté |
| 6 | **Caméra testée** depuis `http://localhost:5173` | la demande d'accès apparaît, le visage est reconnu |
| 7 | **Firmware flashé** (`pio run -e esp8266 -t upload`) | vert clignotant puis vert fixe sur un parcours réel |
| 8 | `docker compose ps` | **19 services** `Up` / `healthy`, aucun `restarting` |
| 9 | `python tools/chaos.py --list` | les 6 scénarios sont connus (répétition facultative) |

Si l'un des points 1 à 3 échoue, **ne pas ouvrir la salle** : lire la section
« NON DÉMONTRABLE » de `preflight.py`, corriger, recommencer. Un service P1
absent (RAG, Loki) est un avertissement, pas un blocage.
