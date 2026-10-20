# RESILIENCE-ALPHA — SHADOW-SETTLE

Système de paiement fictif à confidentialité cryptographique, avec détection
d'anomalies par machine learning et remédiation autonome. Workshop M2 2026.

> **État au 20/10 au soir** — **219 tests** verts, **19 services** Docker,
> **12 failles fermées** et **4 bugs critiques** corrigés. La recette tient en
> une commande : `python tools/preflight.py` (code 0 = démontrable). Détail :
> [`docs/SECURITE.md`](docs/SECURITE.md), [`TODO.md`](TODO.md) §0.5 et
> [`docs/DEMO.md`](docs/DEMO.md) §0.

**MVP complet** : les cinq piliers sont branchés les uns aux autres et la
chaîne tourne de bout en bout sous Docker, sans intervention humaine.

**Consigne additionnelle livrée** : le back-office est protégé par identifiant
+ **reconnaissance faciale** (caméra USB du PC hôte) + mot de passe, avec
habilitations complètes et bandeau LED ESP8266. Voir
[`docs/CONSIGNE-ADDITIONNELLE.md`](docs/CONSIGNE-ADDITIONNELLE.md).

**Nouvelles règles du jury (20/10)** : le SI est un **site vitrine de paiement**
— articles en base, panier, commande, paiement par signature d'anneau,
historique — avec un back d'administration, un **assistant RAG** local et une
**ingestion des alertes SIEM** qui déclenche la remédiation sans humain. Voir
[`TODO.md`](TODO.md) §0.5.

## Ce qui est réellement opérationnel

| Brique | État | Preuve |
|---|---|---|
| `monero-crypto-core` | **bLSAG réel**, key image anti-double-dépense, engagements de Pedersen, audit view-only | 9 tests Rust + 10 tests HTTP |
| `payment-api` | Registre d'outputs chiffrés, leurres gamma, anti-double-dépense, PIN, sonde | `tools/e2e_demo.py`, 43 tests (27 + 16 registre) |
| `ml-detector` | Modèle entraîné sur capture réelle, seuil appris, anti-rebond, diagnostic | `threshold.json`, 12 tests |
| `remediation-engine` | Bus d'états SSE, remédiation asynchrone, verrou, corrélation d'alertes, Ansible réel dans l'image | 27 tests pytest (+ 5 de script), dont 13 d'ingestion SIEM |
| `waf` | Reverse proxy + **déception active** branchée sur le honeypot | 10 tests |
| `shadow-api` | Honeypot borné, journal d'interception consultable | 4 tests |
| `auth-service` | Clé JWT lue dans **Vault** (KV v2), fallback explicite sinon | 9 tests |
| `backoffice` | **Authentification visage + mot de passe** (caméra USB du PC hôte), habilitations, bandeau LED ESP8266 | 29 tests + `tools/backoffice_check.py` |
| `shop-api` | **Boutique** : articles en base, panier, commande, paiement bLSAG, historique | 36 tests (32 + 4 dialecte), PostgreSQL |
| `rag-assistant` | **RAG local** : index BM25 construit au build, Ollama optionnel, sources citées | 16 tests |
| `front-dashboard` | **Vitrine** + **5 quadrants**, anneau SVG, pavé PIN, score ML, audit view-only | `npm run build` |
| `hardware/bridge` | Pont SSE → USB, reconnexion, mode répétition | 9 tests + `--dry-run` |
| `hardware/ring_signer_hsm` | Firmware ESP32, 5 états, parseur de trame | à flasher |
| `hardware/led_bandeau_esp8266` | Firmware **ESP8266** (CLion + PlatformIO), LED bicolore pilotée par le back-office | compile (35 % RAM, 28 % flash), à flasher |
| Observabilité | Prometheus (12 services scrapés, 24 alertes) + **Loki** (logs) + **Alertmanager → remédiation** + Grafana | `localhost:3001` |

**Chaîne P0 mesurée sur Docker** : attaque → détection → remédiation →
transaction de preuve → déception active en **6,6 à 7,2 s** (limite 90 s,
marge ≈ 83 s), sur trois exécutions consécutives.

## Documentation

| Document | Contenu |
|---|---|
| [`docs/RAPPORT.md`](docs/RAPPORT.md) | **Dossier d'ingénierie** : architecture, choix techniques, mesures, limites |
| [`docs/SECURITE.md`](docs/SECURITE.md) | **12 failles fermées, 4 bugs critiques**, mesures par catégorie, recette CYBER |
| [`docs/CONSIGNE-ADDITIONNELLE.md`](docs/CONSIGNE-ADDITIONNELLE.md) | **Réponse à la consigne additionnelle** : exigence par exigence, avec preuve |
| [`docs/GOUVERNANCE.md`](docs/GOUVERNANCE.md) | EBIOS RM, PCA/PRA, NIS2, DORA |
| [`docs/DEMO.md`](docs/DEMO.md) | Runbook de soutenance (3 min, seconde par seconde) |
| [`docs/REGLES-JURY-20-10.md`](docs/REGLES-JURY-20-10.md) | Nouvelles règles du 20/10 : ce qui est livré, prouvé, et ce qui reste |
| [`docs/PLAN-TRAITE-FINALE.md`](docs/PLAN-TRAITE-FINALE.md) | Les trois jours restants, heure par heure, et la définition de « prêt à démontrer » |
| [`docs/INSTALLATION-LOCAL.md`](docs/INSTALLATION-LOCAL.md) | Installation du poste (Windows d'abord) et recette |
| [`docs/mesures-charge.md`](docs/mesures-charge.md) | Mesures de charge : débit, latence, point de rupture |
| [`docs/hardware/CONCEPT-PHYSIQUE.md`](docs/hardware/CONCEPT-PHYSIQUE.md) | Objet physique : rôle, contrat d'état, câblage, bandeau LED |
| [`services/rag-assistant/README.md`](services/rag-assistant/README.md) | Assistant RAG : index BM25, contrat de réponse, limites |
| [`docs/COMPTE-RENDU.md`](docs/COMPTE-RENDU.md) | Journal technique : 12 failles + 4 bugs critiques trouvés et corrigés |
| [`TODO.md`](TODO.md) | Ce qui reste à faire, avec critères d'acceptation |

## Démarrage

Guide complet (Docker, Rust, Python, Node, Vault, dépannage) :
[`docs/INSTALLATION-LOCAL.md`](docs/INSTALLATION-LOCAL.md).

```bash
python tools/init_env.py   # génère .env avec des secrets ALÉATOIRES
docker compose build       # ~6-8 min la première fois (torch, cargo)
docker compose up -d
docker compose ps          # 19 services, tous Up / healthy
```

`tools/init_env.py` est **obligatoire** : il génère `SERVICE_TOKEN`,
`BO_BOOTSTRAP_TOKEN`, `LED_DEVICE_TOKEN` et `TERMINAL_PIN`. Sans jeton de
service, les endpoints d'administration sont **fermés** (503) — fail-closed, pas
de valeur par défaut connue.

| Accès | URL |
|---|---|
| **Boutique (vitrine publique)** | http://localhost:5173 |
| **Back-office** (supervision, habilitations, administration boutique) | http://localhost:5173 → « Espace back-office » |
| API boutique | http://localhost:8087 |
| Noyau cryptographique | http://localhost:8081 |
| Détecteur ML | http://localhost:8083 |
| Moteur de remédiation | http://localhost:8084 |
| Honeypot (shadow-api) | http://localhost:8090 |
| Loki (logs) | http://localhost:3100 |
| Assistant RAG | http://localhost:8091/ask |
| Back-office — API d'authentification | http://localhost:8086 |
| Grafana (admin / admin au 1er démarrage) | http://localhost:3001 |
| Prometheus / Alertmanager | http://localhost:9090 · http://localhost:9093 |
| API de paiement | http://localhost:8080 |
| WAF (point d'entrée attaquant) | http://localhost:8085 |

## La boutique (nouvelles règles du jury)

Le SI est un **site vitrine de paiement**, pas seulement un tableau de bord :

* le **catalogue** vient de PostgreSQL (`products`) — six articles au premier
  démarrage, modifiables par le back ;
* le **panier** et la **commande** sont persistés ; le stock est réservé dans
  une transaction puis restitué si le paiement échoue (pas de survente) ;
* le **paiement** appelle `payment-api /tx/pay` : un output du montant exact
  est émis puis dépensé par une **signature d'anneau bLSAG**. Aucun prestataire
  externe, aucun montant simulé ;
* l'**historique** des achats et des paiements est en base (`orders`,
  `payments`, `payment_events`), lisible par le client (capability token) et par
  l'administrateur ;
* le **back** gère les articles (prix, stock, retrait) et consulte les
  commandes avec leur preuve de paiement.

```bash
python tools/init_env.py          # secrets aléatoires (SERVICE_TOKEN, PIN…)
docker compose up -d
python tools/e2e_shop.py          # achat complet, code de sortie 0
python tools/attack.py shop       # 6 attaques, toutes refusées
curl -s localhost:8087/products | python -m json.tool
curl -s -X POST localhost:8091/ask -H 'Content-Type: application/json' \
     -d '{"q":"combien de temps prend la reprise ?"}'
```

Sans Docker (poste de développement), la pile peut être lancée service par
service ; `python tools/check_compose.py` vérifie d'abord la cohérence
statique du compose (fichiers montés, volumes, dépendances, ports, alertes).

## Authentification du back-office (consigne additionnelle)

Le back-office est **derrière une authentification à deux facteurs** :
identifiant, puis **reconnaissance faciale sur la caméra USB du PC hôte**, puis
mot de passe. Deux échecs bloquent le compte cinq minutes. Un bandeau LED
ESP8266 (CLion + PlatformIO) reflète chaque étape : vert clignotant quand le
visage est reconnu, vert fixe 30 s quand le mot de passe est validé, rouge
10 flashs s'il est refusé, rouge fixe 30 s si le visage ne correspond pas.

```bash
# premier démarrage : enrôler l'administrateur (jeton généré dans .env)
docker compose up -d backoffice front-dashboard
# ouvrir http://localhost:5173 → « Espace back-office » → écran d'enrôlement

python tools/backoffice_check.py  # contrôle de recette, 0 échec attendu
```

Détail complet, exigence par exigence :
[`docs/CONSIGNE-ADDITIONNELLE.md`](docs/CONSIGNE-ADDITIONNELLE.md).
Service, contrats et limites : [`services/backoffice/README.md`](services/backoffice/README.md).
Firmware et recette matérielle : [`hardware/led_bandeau_esp8266/README.md`](hardware/led_bandeau_esp8266/README.md).

## La chaîne, en une commande

```bash
python tools/e2e_demo.py
```

Lance une vraie attaque **via le WAF**, attend la détection et la remédiation,
vérifie qu'une transaction client passe ensuite, et que l'attaquant est
**dérouté vers le honeypot**. Code de sortie 0 = chaîne complète.

```bash
python tools/attack.py poison     # PC Attaquant : les 4 familles d'attaques
python tools/attack.py shop       # 6 attaques sur la boutique (rôle CYBER)
python tools/attack.py list
```

## Le pipeline ML, en une page

**Le piège du sujet** : « une détection par machine learning, et non par de
simples seuils fixes ». Un seuil codé en dur est éliminatoire dans l'esprit du
jury. Ici, tout est appris :

1. `features.py` calcule 6 features, **une seule définition** partagée entre
   l'entraînement et l'inférence (c'était le défaut majeur du squelette :
   entraînement sur des distributions inventées, inférence sur des zéros).
2. `train.py` apprend un `StandardScaler`, un `IsolationForest` et un
   autoencoder PyTorch, puis fixe le seuil sur des fenêtres **hors
   échantillon**.
3. `detector.py` compare le score à ce seuil appris et exige **3 fenêtres
   consécutives** anormales avant de déclencher.

### Ce que la mesure a réellement montré

Quatre erreurs de conception ont été trouvées en recette, et corrigées :

| Défaut | Symptôme | Correction |
|---|---|---|
| Entropie calculée sur les âges **poolés** de la fenêtre | L'entropie passait de 3,0 à 5,4 bits à distribution identique, juste en changeant la cadence | Calcul **par événement** puis moyenne (taille d'anneau constante) |
| `inter_arrival` en écart-type | Une pause de 6 s suffisait à produire un score de 6,2 | **Médiane** des intervalles |
| Sonde de liveness dans la télémétrie | Cadence fixe de 4 s, distribution biaisée | Champ `synthetic`, exclu du calcul |
| Entropie comme feature principale | AUC 0,56 : elle sature à 10 leurres par transaction | Remplacée par l'**âge médian des leurres** (AUC 1,00) |

`analyze_separation.py` mesure l'AUC de chaque feature sur 1 140 fenêtres :

| Feature | AUC (leurres empoisonnés) |
|---|---|
| `decoy_median_age_days` | **1,00** (séparation parfaite) |
| `ring_age_skewness` | 0,94 (complémentaire) |
| les 4 autres | 0,50 (bruit pour cette attaque, utiles aux autres) |

Justification physique : un wallet Monero tire ses leurres selon une gamma
biaisée vers les outputs récents — **3,67 j ± 0,16** mesuré sur ce système. Un
attaquant qui empoisonne les leurres les tire uniformément dans le registre —
**18,9 j ± 1,4**. Aucun chevauchement. C'est un résultat honnête à assumer en
Q&A : une seule feature discrimine l'attaque principale, et elle est
physiquement justifiée.

## La cryptographie, en une page

`monero-crypto-core` implémente un modèle Monero **simplifié mais réel** sur
Ristretto255 :

| Primitive | Implémentation |
|---|---|
| Adresse one-time | `P = H_s(rA)G + B`, `R = rG` (ECDH) |
| Clé dérivée par le porteur | `x = H_s(aR) + b` |
| Key image (anti-double-dépense) | `I = x·H_p(P)`, hash-**vers-le-groupe** (Elligator) : personne ne connaît le log discret, donc pas de forge |
| Signature d'anneau liée | **bLSAG** sur `n` clés, message lié aux engagements |
| Engagements | `C = aG + rH`, homomorphes, aveugles sommant à zéro |
| Confidentialité du montant | masque `Keccak("amount" ‖ shared)` appliqué au montant |
| Audit **view-only** | `GET /audit/last` : lit le montant **sans** la Spend Key |

Deux écarts assumés, publiés dans `/healthz` (`not_implemented`) et dans le
dossier de gouvernance :

- **bLSAG** au lieu de CLSAG → signature ~2× plus grosse, même modèle de
  sécurité ;
- **pas de Bulletproofs** → un montant négatif modulo l pourrait passer la
  vérification d'équilibre.

## Tests

**Windows (PowerShell) :**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\tools\run_tests.ps1 -Fast
```

**macOS / Linux :**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
./tools/run_tests.sh --fast
```

**219 tests** : 195 pytest, 5 tests de script, 9 Rust, 10 HTTP. Par brique :
bus d'états et incidents (19), ingestion SIEM (13), détecteur ML (12), API de
paiement (27 + **16 registre**), noyau Rust unitaire (9) et HTTP (10), WAF (10),
pont hardware (9), honeypot (4), auth-service (9), **back-office (29)**,
**boutique (32 + 4 dialecte)**, **assistant RAG (16)**, plus le contrôle
d'encodage des fichiers livrés.

> Les deux scripts lancent chaque suite **dans le dossier de son service**.
> C'est volontaire : plusieurs services exposent un `main.py`, et un
> `pytest services/` global ferait résoudre `import main` vers le premier module
> homonyme importé.

## Ce qui reste à faire

**Fait depuis l'audit du 20/10** (ne plus le présenter comme un reste) :

- **corrélation des alertes** : trois à cinq alertes en cascade ouvrent **un**
  incident, pas cinq (`test_alert_ingest.py`) ;
- **idempotence** : rejeu d'une alerte, d'un balayage `pending` ou d'une
  commande ne produit jamais de double effet ;
- **commandes `pending`** : balayage au démarrage et toutes les 30 s, stock
  restitué, réconciliation si le paiement local est déjà `paid` ;
- **images produit** : six SVG locaux servis par `/static`, `image_url` relatif
  dans `GET /products` ;
- **dernier administrateur** : impossible de supprimer ou rétrograder le dernier
  compte `admin` (verrou testé) ;
- **pré-vol** : `python tools/preflight.py` vérifie `.env`, le PIN, la santé des
  services, le catalogue et la propreté du dépôt.

**Reste ouvert, assumé et écrit :**

- **Bulletproofs** (preuves de domaine) : seule vraie faille cryptographique
  restante. Priorité 1 du dossier de gouvernance.
- **AppRole Vault** : le playbook utilise aujourd'hui un token de service.
- **Redémarrage réel du registre PostgreSQL** sur la pile Docker : le journal et
  le rechargement sont testés (16 tests), la persistance de bout en bout reste à
  mesurer sur l'hôte.
- **RAG Ollama** : service livré (BM25, 16 tests, quadrant dans le dashboard) ;
  Ollama n'est pas branché sur le PC IA, le mode `extractive` est le défaut.
- **Objet physique** : firmware ESP8266 écrit et compilé, **à flasher** ; modèle
  3D v1 jamais imprimé.
- **Détection de vivacité** du visage : non implémentée, publiée dans `/healthz`
  (`liveness: not_implemented`).
- **TLS** : tout est en HTTP sur `localhost` (la caméra l'exige) ; pas de HSTS
  ni de certificat, même sur le back-office.
- **Mesures Docker** : les chiffres du §« Chaîne P0 » viennent de la pile de
  démonstration ; ils ne sont pas refaits sur ce poste (Docker absent).
- **Scan de dépendances** : `pip-audit`, `cargo audit` et `npm audit` ne sont
  pas encore passés en recette.
- **Recalibrage ML** sur le PC hôte de démonstration (7 min, obligatoire).

Détail complet, avec chiffres et justifications :
[`docs/RAPPORT.md`](docs/RAPPORT.md), [`TODO.md`](TODO.md) et
[`docs/GOUVERNANCE.md`](docs/GOUVERNANCE.md).

## Priorités

- **P0** — chaîne attaque → ML → Ansible → preuve → déception : **fait et mesuré**.
- **P1** — honeypot, WAF, observabilité, dashboard, dossier EBIOS/PCA-PRA/DORA,
  **consigne additionnelle (visage + LED)** : **fait**.
- **P2** — hardware, RAG Ollama. L'objet physique ne doit **jamais** être
  nécessaire à la démo.

## Objet physique

Rôle, scénario de démonstration, contrat d'état et câblage :
[`docs/hardware/CONCEPT-PHYSIQUE.md`](docs/hardware/CONCEPT-PHYSIQUE.md).
Montage et impression : [`hardware/README.md`](hardware/README.md).

## Sécurité du livrable

Aucun secret en clair dans le ZIP (règle éliminatoire). `.gitignore` couvre
`.env`, `secrets/`, les clés et les artefacts de build. Vérifier avant de
zipper :

```bash
python tools/preflight.py      # vérifie déjà qu'aucun secret n'est suivi par git
git secrets --scan             # si l'extension git-secrets est installée
grep -rn "hvs\.\|BEGIN.*PRIVATE KEY" --include="*.py" --include="*.yml" --include="*.md" .
```
