# TODO — ce qui reste à faire

> État au **20 octobre 2026, soir** (fin de la journée 2 sur 5). Le MVP est
> fonctionnel et mesuré : **219 tests** (195 pytest verts sur ce poste, 5
> scénarios, 9 unitaires Rust, 10 HTTP), chaîne P0 en 6,6 / 7,1 / 7,2 s,
> 19 services.
> **Plan des trois jours restants** : [`docs/PLAN-TRAITE-FINALE.md`](docs/PLAN-TRAITE-FINALE.md).
> Il ne reste presque plus de code : il reste de la **preuve sur le PC hôte**
> (Docker, caméra, ESP8266) et de la **répétition**. Les points ouverts ci-dessous
> sont classés par ce qui bloque réellement la démonstration.
> Ce fichier liste **uniquement ce qui n'est pas fait**, avec un critère
> d'acceptation vérifiable pour chaque ligne. Les points fermés restent visibles
> au §0.6 pour que personne ne les rouvre par ignorance.

Légende : `[ ]` à faire · `[~]` partiel · `[x]` fait
Effort : **S** ≤ 2 h · **M** ≤ 1 j · **L** ≤ 3 j · **XL** > 3 j

---

## 0.5 Nouvelles règles du jury — écart, livré, restant

Le jury a précisé le 20/10 ce qu'il attend. Six exigences, dont trois
changeaient l'architecture :

| # | Exigence du jury | État | Preuve |
|---|---|---|---|
| N1 | SI de paiement **comme un site vitrine**, connecté à une base | **livré** | `services/shop-api` (PostgreSQL) + `front-dashboard/src/shop/Storefront.jsx` |
| N2 | **Front** qui présente des articles (en base) | **livré** | `GET /products`, catalogue de 6 articles en base |
| N3 | **Back** qui gère tout cela | **livré** | `front-dashboard/src/shop/AdminShop.jsx` + `/admin/products`, `/admin/orders` |
| N4 | **Paiement en ligne** avec stockage, **historique** des achats et des paiements | **livré** | `POST /checkout` → `payment-api /tx/pay` (bLSAG) → tables `orders`, `payments`, `payment_events` |
| N5 | SI **invulnérable**, aucune faille volontaire | **livré (1ʳᵉ passe)** | §0.5.1 : 7 failles réelles fermées |
| N6 | **IA** : RAG, surveillance des alertes SIEM, remédiation autonome | **livré (socle)** | `services/rag-assistant`, `POST /alert` + Alertmanager → playbook |

### 0.5.1 Failles réelles fermées (N5)

Ces sept points n'étaient pas des « failles de démonstration » : c'étaient des
trous qu'un jury CYBER exploite en quelques minutes.

| Faille | Exploit avant correction | Correction |
|---|---|---|
| `/auth/login` de `auth-service` acceptait **n'importe quel** `api_secret` | un JWT valide pour tout identifiant connu | vérification SHA-256 + `MERCHANT_SECRETS`, fail-closed 503 |
| `/admin/*` de `payment-api` et du WAF **ouverts** | quarantaine de 25 outputs, anneau à 64, déception désarmée | `SERVICE_TOKEN` obligatoire, comparaison à temps constant |
| `/incident` et `/state/report` **ouverts** | playbook déclenché par n'importe qui (déni de service) | idem |
| **PIN `1234` codé en dur** | terminal ouvert avec une valeur publique | `TERMINAL_PIN` généré par `tools/init_env.py`, blocage après 5 échecs |
| **Anneau non borné** sur `/tx/submit` | 100 000 leurres = saturation CPU | plafond à 64 leurres |
| `/ingest` du détecteur **ouvert** | évasion : diluer la fenêtre pour empêcher la détection | `SERVICE_TOKEN` obligatoire |
| **Entrées non strictes** (boutique) | quantité `"3"` convertie silencieusement ; SKU `X'; DROP TABLE …` stocké | `strict=True` sur les entiers, motif sur le SKU → 422 |

- [x] **Audit de sécurité systématique** — M
  `docs/SECURITE.md` : **7 failles fermées** (chacune avec son test), mesures par
  catégorie (auth, autorisation, données, en-têtes, secrets), **7 limites
  assumées et écrites**, et une recette CYBER en 6 commandes `curl`.
  **Reste** : un audit externe, hors périmètre étudiant.

- [ ] **Scan de dépendances** — S
  `pip-audit` sur les `requirements.txt`, `cargo audit` sur le noyau,
  `npm audit` sur le dashboard.
  **Acceptation** : aucune vulnérabilité `HIGH`/`CRITICAL` non justifiée par
  écrit.

- [ ] **TLS sur le back-office et la boutique** — M
  Aujourd'hui tout est en HTTP sur `localhost` (la caméra l'exige). En
  production, TLS + HSTS. Voir §1.0.

### 0.5.2 Ce qui reste sur la boutique (N1–N4)

- [~] **Test de bout en bout de l'achat sous Docker** — S
  `tools/e2e_shop.py` est **écrit** (catalogue → panier → paiement réel →
  commande `paid` → historique → back, code de sortie). Il n'a jamais tourné
  contre la pile : c'est la première chose à faire le 21/10.
  **Acceptation** : `chain_complete: true`, commande visible dans
  `/admin/orders`, stock décrémenté.

- [ ] **Vérifier la persistance PostgreSQL au redémarrage** — S
  `docker compose restart shop-api db-postgres` puis relire les commandes.
  **Acceptation** : l'historique et le stock sont identiques après
  redémarrage ; aucune commande perdue.

- [~] **Test de concurrence sur le stock** — S
  Le comportement est prouvé par un test unitaire (5 threads sur le dernier
  article : un seul `201`, les autres `409`, stock 0). Il manque la mesure
  **sur la pile** : deux `curl` simultanés sur le dernier article.
  **Acceptation** : une seule commande `paid`, l'autre en `409`, stock jamais
  négatif.

- [x] **Migration du registre d'outputs vers PostgreSQL** — fait
  `services/payment-api/registry.py` (journal PostgreSQL, rechargement au
  démarrage, 16 tests). Voir §1.3 pour ce qui reste à vérifier sur la pile.

- [x] **Images produit** — fait le 20/10 au soir
  Six SVG servis par `shop-api` sous `/static/products/`, `image_url` en base,
  affichage dans la vitrine et vignettes dans le back. 3 tests.
  **Acceptation remplie en test** : `GET /static/products/<sku>.svg` → 200 ;
  le catalogue expose une image pour les 6 articles.

### 0.5.3 Ce qui reste sur l'IA (N6)

- [ ] **Faire tourner Ollama sur le PC IA** — M
  `ollama pull llama3.1:8b` puis `OLLAMA_URL=http://<pc-ia>:11434`.
  **Acceptation** : `/ask` renvoie `engine: ollama:llama3.1:8b` et une réponse
  rédigée citant `docs/RAPPORT.md`.

- [x] **Brancher les logs (Loki) sur le SIEM** — fait
  `loki`/`promtail` configurés, 4 règles de logs poussées à Alertmanager,
  3 actionnables par le moteur (§2.2). Reste à vérifier dans Grafana Explore.
  **Acceptation** : une erreur de `payment-api` déclenche une alerte Loki →
  Alertmanager → `POST /alert` → playbook.

- [x] **Corrélation multi-alertes** — fait le 20/10 au soir
  Une rafale d'alertes actionnables produit **un seul** incident, avec la liste
  corrélée dans `detail.alerts` (visible dans `/state`, `/events`, `/history`).
  Une alerte tardive est rattachée à l'incident en cours au lieu d'être perdue ;
  hors fenêtre (`CORRELATION_WINDOW_SECONDS`, 10 s), elle est ignorée avec un
  motif explicite. 9 tests dédiés.

- [x] **Scénario de démonstration « attaque sur la boutique »** — fait
  `tools/attack_shop.py` : **6 familles** (quantités, panier forgé, injection
  SQL par SKU, énumération d'historique, course sur le stock, falsification de
  prix). Mesuré : 6/6 refusées, 0 commande frauduleuse, stock jamais négatif,
  service debout en 0,8 s.

---

## 0.6 Audit de code — ce qui a été fermé, ce qui reste

L'audit a trouvé 4 bugs critiques et 12 moyens. **Les 4 critiques et 10 des 12
moyens sont fermés** le 20/10 au soir, chacun avec un test de régression.
Voir `docs/SECURITE.md` §2 bis et §6.

### 0.6.1 Commandes bloquées en `pending` — fermé

- [x] **Balayage des `pending` au démarrage et périodiquement** — fait
  `SHOP_PENDING_TTL_SECONDS` (120 s) et `SHOP_PENDING_SWEEP_SECONDS` (30 s) ;
  réclamation atomique (`UPDATE ... WHERE status='pending'`), restitution du
  stock une seule fois, événement `expired`, réconciliation si le paiement est
  `paid`. 4 tests.
- [x] **`/tx/pay` idempotent par `reference`** — fait
  Table `registry_payments`, cache mémoire borné, verrou par référence ; un
  rejeu renvoie la même key image avec `replayed: true`, un montant différent
  donne 409. 4 tests, dont deux rejeux simultanés.

### 0.6.2 Registre : reconnexion — fermé

- [x] **Reconnexion avec backoff + compteur de pertes** — fait
  Backoff 1 s → 30 s dans un thread démon (le chemin critique ne bloque
  jamais), `available` repasse à `True`, `lost_writes` et
  `reconnect_attempts` publiés dans `/healthz` et `/metrics`. Reste à vérifier
  en réel : `docker compose stop db-postgres`, quelques paiements, `start`.

### 0.6.3 Itération de structures mutées — fermé

- [x] **Snapshot sous verrou** — fait
  `_outputs_lock` + `_snapshot_outputs()` pour toutes les lectures de `OUTPUTS`
  (`/metrics`, `/decoys`, `/outputs`, quarantaine, purge de la sonde) ;
  `pin_attempts` lu sous `_pin_lock`. Test de mutation concurrente (200
  itérations). Reste la mesure 10 min sous charge, sur la pile.

### 0.6.4 `StateBus.publish` depuis le threadpool — fermé

- [x] **Verrou + `loop.call_soon_threadsafe`** — fait
  `publish` est thread-safe, `report_state` est `async def`, la boucle est
  mémorisée. Test : 20 publications depuis 4 threads, `seq` strictement
  croissants, abonné réveillé sans attendre le ping.

### 0.6.5 Garde « dernier administrateur » — fermé

- [x] **Transaction + compter les admins avec visage** — fait
  Garde dans `BEGIN IMMEDIATE`, cible relue sous verrou, seuls les admins
  actifs **avec visage** comptent. Protégé sur `PATCH`, `DELETE` et le retrait
  du visage. 4 tests, dont deux `PATCH` concurrents sur deux connexions.

### 0.6.6 Divers — 2 points restent ouverts

- [x] `assert` → exception explicite (`shop-api`, `backoffice`).
- [x] `db.run` : paramètre `returning=` explicite, plus d'heuristique.
- [x] `_auto_probe_loop` borné (`PROBE_OUTPUT_RETENTION`, 64) et purge au
  démarrage.
- [x] `/tx/pay` rate-limité par IP (`TX_PAY_RATE_LIMIT`, désactivé par défaut
  pour ne pas fausser les mesures de charge) → 429 + `Retry-After`.
- [x] `decoy_ids` borné à 64 **avant** tout traitement (422 Pydantic).
- [x] `_replenish_pool` appelé depuis `/decoys` et `/tx/pay` sous seuil.
- [ ] `random.seed(1337)` pour les leurres : reproductible, assumé pour la
  démonstration. À remplacer par `SystemRandom` si l'anonymat est défendu.
  **Décision** : on garde pour la démo, et on l'écrit en Q&A.
- [ ] `test_alert_ingest.py` met `fingerprint` dans `labels`, alors que
  l'Alertmanager réel le place au niveau de l'alerte. Le repli `name:status`
  est donc utilisé en production : le test ne couvre pas le vrai format.
  **Effort S, à faire le 21/10** si la corrélation doit être montrée en réel.

## 0. Avant la soutenance — bloquant, à faire la veille

Ces tâches ne sont pas du développement : sans elles, la démo peut échouer.
**Le plan complet des trois jours est dans
[`docs/PLAN-TRAITE-FINALE.md`](docs/PLAN-TRAITE-FINALE.md).**

- [ ] **Lancer le pré-vol** — S (5 s)
  ```powershell
  python tools/preflight.py
  ```
  Vérifie les secrets (sans les afficher), le PIN transmis au conteneur, les 19
  services, le catalogue illustré, le RAG, et refuse un dépôt sale.
  **Acceptation** : code 0, « DÉMONTRABLE ».

- [ ] **Recalibrer le ML sur le PC hôte de démonstration** — S (7 min)
  Le modèle actuel est calibré sur le poste de développement (1 129 fenêtres).
  Un seuil calibré ailleurs produit des faux positifs : c'est arrivé trois fois.
  ```bash
  cd services/ml-detector
  ../../.venv/bin/python capture_baseline.py --seconds 420 --out baseline.jsonl
  ../../.venv/bin/python train.py --from-capture baseline.jsonl --epochs 600
  cd ../.. && docker compose build ml-detector && docker compose up -d ml-detector
  ```
  **Acceptation** : `curl -s localhost:8083/healthz` → `"model": "ready"` ;
  `python tools/e2e_demo.py` → `chain_complete: true`.

- [ ] **Trois répétitions générales consécutives** — S (5 min)
  **Acceptation** : 3 × `chain_complete: true`, `recovery_seconds < 90`.

- [ ] **Vérifier l'absence de secret dans le ZIP** — S
  **Acceptation** :
  ```bash
  git status --short          # doit être vide
  git ls-files | xargs grep -l "hvs\.\|BEGIN.*PRIVATE KEY" 2>/dev/null
  # doit ne rien renvoyer
  ```

- [ ] **Tester le dashboard dans le navigateur de la salle** — S
  Le build fonctionne, mais le rendu (taille de police, lisibilité à distance)
  n'a pas été validé sur un vidéoprojecteur.
  **Acceptation** : badge d'état, score ML et anneau lisibles à 3 m.

- [ ] **Répéter le Clip Chrono** — S
  **Acceptation** : 60 s continues, sans coupe, avec `docs/DEMO.md` §2.

- [ ] **Contrôler le back-office et la caméra** — S (2 min)
  ```bash
  python tools/backoffice_check.py     # 0 échec attendu
  ```
  Vérifie que le moteur facial est le **vrai** (pas le stub), qu'un
  administrateur est enrôlé, qu'aucun compte n'est bloqué et que le bandeau
  LED répond. Faire ensuite un parcours complet à la main, dans le navigateur
  du PC hôte, **avec la caméra branchée sur ce PC**.
  **Acceptation** : identifiant → visage reconnu (vert clignotant) → mot de
  passe (vert fixe 30 s) → dashboard ; un visage inconnu donne rouge fixe 30 s.

---

## 1. Priorité haute — points perdus à la grille

### 1.0 Consigne additionnelle — authentification visage du back-office

Livré : `services/backoffice` (port 8086), page de connexion du dashboard,
écran d'habilitations, firmware `hardware/led_bandeau_esp8266`, scrape
Prometheus et 5 alertes. Détail exigence par exigence :
[`docs/CONSIGNE-ADDITIONNELLE.md`](docs/CONSIGNE-ADDITIONNELLE.md).

Ce qui reste :

- [ ] **Flasher le firmware du bandeau** — S
  CLion + PlatformIO, `hardware/led_bandeau_esp8266/` :
  `cp src/secrets.example.h src/secrets.h`, renseigner le SSID du point d'accès
  `RESILIENCE-G<n>` et l'IP de la passerelle, puis `pio run -t upload`.
  **Acceptation** : les 5 tests du `README.md` §4 passent (vert clignotant <
  400 ms après le scan, vert fixe 30 s, rouge ×10, rouge fixe 30 s, extinction
  sans back-office).

- [ ] **Enrôler l'administrateur de démonstration** — S
  `BO_BOOTSTRAP_TOKEN` dans `.env`, ouvrir http://localhost:5173, enrôler
  visage + mot de passe, **puis retirer le jeton** et redémarrer le service.
  **Acceptation** : `python tools/backoffice_check.py` → 0 échec ; `/healthz`
  → `bootstrap_required: false`, `face_engine: ready`, `demo_only: false`.

- [ ] **Détection de vivacité (anti-photo)** — L
  Une photo imprimée tenue devant la caméra passe aujourd'hui. Publié dans
  `/healthz` (`liveness: not_implemented`).
  **Piste** : challenge de mouvement (cligner / tourner la tête) vérifié par
  variation de descripteur, ou modèle anti-spoofing ONNX dédié.
  **Acceptation** : une photo de l'utilisateur enrôlé est **refusée** ; le
  visage réel est accepté ; `liveness` disparaît de `not_implemented`.

- [ ] **HTTPS sur le back-office** — M
  Aujourd'hui la caméra ne fonctionne qu'en `localhost` (contexte sécurisé) ;
  une IP LAN la fait refuser par le navigateur.
  **Acceptation** : certificat auto-signé + `https://resilience-g1.local:8086`,
  parcours complet depuis un second poste du point d'accès.

- [ ] **Répéter le parcours d'authentification dans le Clip Chrono** — S
  Il ajoute ~20 s au scénario de 60 s. Réécrire `docs/DEMO.md` §2 en
  conséquence et rechronométrer.
  **Acceptation** : 60 s continues, sans coupe, avec les deux facteurs.

### 1.1 Bulletproofs (preuves de domaine) — XL

**Pourquoi** : seule vraie faiblesse cryptographique. Sans preuve de domaine, un
montant négatif modulo l satisfait `∑C_in − ∑C_out = 0`. Aujourd'hui publié dans
`/healthz` (`not_implemented`), ce qui est honnête mais reste un trou.

**Où** : `services/monero-crypto-core/src/main.rs`
- ajouter une dépendance `bulletproofs` (crate Dalek) ;
- endpoint `POST /crypto/range-proof` et vérification dans `/crypto/ring-verify` ;
- lier la preuve à l'engagement de Pedersen déjà produit.

**Acceptation** :
- un test Rust prouve qu'un montant `2^64 - 1 + 1` (négatif modulo l) est
  **refusé** ;
- un montant normal passe ;
- `GET /healthz` ne liste plus `bulletproofs_range_proof` dans
  `not_implemented` ;
- la latence de `/tx/submit` reste sous 100 ms pour un anneau de 11.

**Risque** : la vérification d'une preuve de 64 bits coûte ~1-2 ms. Cela change
`ring_verify_cpu_us`, donc il faudra **recapturer la baseline ML** après.

### 1.2 AppRole Vault — S

**Pourquoi** : le playbook utilise `VAULT_TOKEN` (token de service). La grille
parle d'« accès du moteur de remédiation maîtrisés » (Axe 2).

**Où** : `ansible/remediation_playbook.yml`, `docker-compose.yml`,
`observability/` (script d'init).

- créer la politique `remediation-policy` (écriture limitée à `secret/data/jwt`) ;
- activer `auth/approle`, créer un rôle, générer `role_id`/`secret_id` ;
- injecter ces deux valeurs par `.env` et retirer `VAULT_TOKEN` du compose ;
- conserver le fallback `VAULT_TOKEN` pour le poste de dev, mais le **signaler**
  dans `/healthz`.

**Acceptation** : `ansible-playbook` réussit sans `VAULT_TOKEN` ; la tâche
AppRole n'est plus `skipped` ; `auth-service /healthz` → `jwt_key_source: vault`.

### 1.3 Registre persistant (PostgreSQL) — fait

`services/payment-api/registry.py` : journal d'écriture PostgreSQL ; la mémoire
reste la source de lecture (latence du chemin critique). Tables `outputs` et
`key_images`, `CREATE TABLE IF NOT EXISTS` au démarrage, rechargement au
démarrage **avant** toute émission — sinon `_registry_seq` repart de zéro et
écrase `output_1`. 8 tests dédiés.

Repli **explicite** : sans `DATABASE_URL`, le registre reste en mémoire et
`/healthz` publie `registry.durable: false`, avec l'alerte
`RegistreNonPersistant`. Un registre non persistant silencieux serait exactement
la panne qu'on cherche à supprimer.

Reste à vérifier sur la pile démarrée :

- [ ] **Redémarrage réel** — S
  Acheter un article, puis `docker compose restart payment-api`.
  **Acceptation** : `/healthz` → `registry.durable: true` et `outputs` > 0 ;
  l'historique de la boutique est intact ; un rejeu renvoie toujours 409.

- [ ] **Mesurer `_replenish_pool()`** — S
  Il parcourt tous les outputs à chaque `/outputs?unspent=true`. Négligeable à
  500 outputs, pas à 50 000.
  **Acceptation** : passer en `COUNT(*)` SQL si la mesure dépasse 20 ms.

### 1.4 RAG Ollama — fait (socle)

`services/rag-assistant` (8091) : index **BM25** construit à la construction de
l'image sur `docs/` + `README.md` + `TODO.md` (14 documents, 597 passages),
`POST /ask` avec sources citées, 14 tests.

**Écart assumé** : pas de base vectorielle (Chroma/FAISS) ni de LangChain.
BM25 donne la même qualité sur un corpus de 14 documents, sans modèle à
télécharger au build ni dépendance lourde. C'est écrit dans
`services/rag-assistant/rag.py`.

**Acceptation remplie** : la question de référence répond « 6,6 à 7,2
secondes » **en citant** `docs/RAPPORT.md`, et c'est vérifié par un test
(`test_la_reponse_donne_la_duree_et_cite_le_rapport`).

Reste :

- [ ] **Faire tourner Ollama sur le PC IA** — M
  `ollama pull llama3.1:8b`, puis `OLLAMA_URL=http://<pc-ia>:11434`.
  **Acceptation** : `/ask` renvoie `engine: ollama:llama3.1:8b` et une réponse
  rédigée citant `docs/RAPPORT.md`. Sans lui, le mode extractif reste actif.

- [x] **Quadrant « Assistant » dans le dashboard** — S
  `front-dashboard/src/RagAssistant.jsx`, 5ᵉ quadrant de la supervision :
  question, réponse, sources avec score BM25, et **le moteur affiché**
  (`ollama:llama3.1:8b` ou `extractive`). Si Ollama est muet, l'écran l'écrit
  au lieu de faire semblant. Build : 201,76 kB.

---

## 2. Priorité moyenne — qualité et crédibilité

### 2.1 Métriques PostgreSQL — fait

`postgres-exporter` ajouté (9187), 12ᵉ cible Prometheus, **3 alertes** :
`BaseDeDonneesIndisponible`, `PoolPostgresSature`, `BasePostgresVolumineuse`.
Ce n'était plus optionnel : PostgreSQL porte désormais les commandes, les
paiements et le registre d'outputs.

Reste :

- [x] **Panneaux Grafana « base de données »** — fait le 20/10 au soir
  Connexions actives (`pg_stat_database_numbackends`), taille de la base
  (`pg_database_size_bytes`), transactions longues
  (`pg_stat_activity_max_tx_duration`). `pg_stat_statements` n'est pas activé :
  le panneau ne prétend pas mesurer les « requêtes lentes », sa description
  l'écrit. **Acceptation remplie** : 3 panneaux, JSON valide, 13 panneaux au
  total, aucun chevauchement.

### 2.2 Loki / Promtail — fait

Configurés pour de vrai (`observability/loki.yml`, `promtail.yml`,
`loki-rules.yml`) : Promtail lit les logs de tous les conteneurs via le socket
Docker **en lecture seule**, Loki les stocke 30 jours, Grafana Explore les
affiche (source Loki provisionnée, liée à Prometheus), et **4 règles de logs**
poussent à Alertmanager → remédiation (`PaiementRefuse`,
`JetonServiceAbsentDansLesLogs`, `RemonteeDeBouclier`, `ErreurApplicative`).

Reste à vérifier sur la pile démarrée :

- [ ] **Vérifier les logs dans Grafana Explore** — S
  Ouvrir `localhost:3001` → Explore → source Loki → `{service="payment-api"}`.
  **Acceptation** : les lignes défilent, le niveau `ERROR` est filtrable, et
  `{service="shop-api"} |= "commande"` montre les achats.

- [ ] **Provoquer une alerte de log** — S
  `docker compose stop monero-crypto-core`, puis acheter un article.
  **Acceptation** : `PaiementRefuse` apparaît dans Alertmanager (9093) et
  `POST /alert` du moteur renvoie `acted: 1`.

### 2.3 Test de charge — fait

`tools/load_test.py` : 1/5/10/20 clients concurrents, débit, p50/p95/p99,
taux d'erreur, point de rupture, rapport Markdown dans
`docs/mesures-charge.md`. Trois cibles : `payment`, `shop`, `rag`.

**Résultats** : paiement 630 req/s sans rupture ; RAG 336 req/s ; **boutique =
point de rupture** (~130 ms par achat complet, > 1 s à 5 clients simultanés,
à cause de la signature d'anneau — 0 % d'erreurs).

**Ce que la mesure a corrigé** : `/ask` et `/healthz` du RAG attendaient le
timeout d'Ollama (0,5 req/s, p95 2 318 ms). Disjoncteur + sonde sans appel
réseau : **×670 de débit**. Détail dans `docs/mesures-charge.md`.

Reste : refaire la mesure sur le PC hôte, avec PostgreSQL et Docker.

### 2.4 Chaos engineering — fait

`tools/chaos.py` : 6 scénarios, chacun avec un **invariant métier** (pas
seulement un code HTTP), un statut et un code de sortie. Le script redémarre
systématiquement ce qu'il a arrêté, même en cas d'exception.

| Scénario | Invariant vérifié |
|---|---|
| `ml` | arrêter le détecteur ne casse pas le paiement (le ML observe) |
| `remediation` | le paiement continue, `/state` injoignable comme attendu |
| `crypto` | 503/502 explicite, `crypto_core: degraded` — jamais un 500 nu |
| `shop` | `payment-api` reste sain sans la boutique (silos) |
| `token` | sans jeton, 503 fail-closed (vérifié en réel) |
| `rag` | arrêter l'assistant ne touche ni boutique ni paiement |

**Vérifié en réel** : `token` → 503 sur les trois routes d'administration.
**Non testés ici** : `ml`, `remediation`, `crypto`, `shop`, `rag` exigent
`docker compose` (absent de ce poste) ; le script les déclare explicitement
`NON TESTÉS` au lieu de faire semblant.

Reste à lancer sur le PC hôte :

```bash
python tools/chaos.py              # les 6 scénarios
python tools/chaos.py --only ml    # un seul
```

**Acceptation** : 6/6 `PASS` sur la pile Docker.

### 2.5 Le quadrant « console Ansible » du dashboard — partiel

Le quadrant affiche désormais : l'état (`en cours · 3,2 s`), le diagnostic, les
alertes corrélées, `ansible_rc`, et l'extrait `ansible_stdout_tail` de
l'incident précédent. **La limite restante est côté moteur** : `_run_playbook`
capture la sortie en bloc (`capture_output=True`) et n'écrit `/history` qu'à la
fin ; il n'y a donc rien à afficher *pendant* l'exécution.

**Correctif (½ h)** : lancer `ansible-playbook` en `Popen`, lire stdout ligne à
ligne, republier `REMEDIATING` avec `detail.ansible_stdout_tail` (ring buffer
~30 lignes) toutes les ~300 ms. Le front consomme déjà ce champ s'il apparaît.

**Acceptation** : pendant `REMEDIATING`, les noms de tâches Ansible défilent.

---

## 3. Priorité basse — objet physique

### 3.1 Flasher le firmware — S

`hardware/ring_signer_hsm/ring_signer_hsm.ino` n'a jamais été flashé.

**Acceptation** : `python hardware/bridge/hardware_bridge.py --port /dev/ttyUSB0
--replay` → les 4 états défilent sur l'anneau, sans reboot de l'ESP32.

### 3.2 Mesurer le terminal et imprimer le socle — M

`hardware/models/socle_terminal.scad` est une v1 paramétrique jamais imprimée.

**Acceptation** : les 4 pièces s'emboîtent avec le terminal réel ; le diffuseur
PETG laisse passer la lumière.

### 3.3 Recette en 7 tests du `hardware/README.md` — S

Les 7 tests sont écrits mais n'ont pas été exécutés sur matériel.

---

## 4. Dette technique assumée

| Élément | Pourquoi c'est acceptable | Quand s'en occuper |
|---|---|---|
| `services/ml-detector/baseline.jsonl` non versionné | propre à chaque poste, reproductible en 7 min | jamais (par conception) |
| `tools/fake_ansible.py` et `local-dev/crypto_shim.py` | marqués non livrables, utiles pour un poste sans Docker | garder |
| `ansible.cfg` absent | Ansible fonctionne sans | si on ajoute des rôles |
| Pas de CI (GitHub Actions) | le projet est un rendu, pas un produit | si le dépôt devient public |
| Pas de `pre-commit` | 219 tests suffisent | optionnel |

---

## 5. Ce qui est explicitement hors périmètre

Ne pas perdre de temps dessus, c'est un choix documenté :

- **Monero réel** (mainnet, nœud, sync) : le sujet parle d'un SI fictif.
- **RGPD** : **deux traitements réels** depuis la consigne additionnelle —
  descripteurs faciaux (données biométriques, art. 9) et journal d'audit du
  back-office. Le dossier `docs/GOUVERNANCE.md` §8.4 décrit la minimisation,
  la base légale et ce qui manque (registre de traitement, DPO, AIPD). Ce n'est
  pas « aucune donnée personnelle » : c'était l'erreur du document précédent.
- **Homologation / pen-test formel** : hors périmètre étudiant.
- **Plan de formation, cellule de crise** : hors périmètre technique.
- **MFA** : le back-office **est** à deux facteurs (visage + mot de passe)
  depuis la consigne additionnelle. Le **terminal de paiement**, lui, n'a qu'un
  PIN : c'est la surface d'attaque volontairement faible qui alimente le ML, et
  c'est écrit ainsi dans `docs/GOUVERNANCE.md` §8.1.
- **WAF type ModSecurity/CRS** : nous avons écrit un proxy de déception sur
  mesure, ce qui est plus démontrable qu'une image OWASP non configurée.

---

## 6. Ordre d'exécution — les trois jours qui restent

Le plan détaillé, heure par heure, est dans
[`docs/PLAN-TRAITE-FINALE.md`](docs/PLAN-TRAITE-FINALE.md).

```
J3 (21/10)  Docker : build, up, preflight, e2e_demo ×3, e2e_shop, attaques,
            chaos 6/6, charge PostgreSQL, Loki, registre, idempotence,
            recalibrage ML, caméra + firmware + hotspot
J4 (22/10)  Démonstration : storyboard 60 s ×3, Clip Chrono, Q&A,
            Ollama (si PC IA), AppRole (si tout est vert), répétition en salle
J5 (23/10)  Livraison : gel, suite complète, secrets, ZIP, PDF, dernière
            répétition, plan B, tag v1.0-demo
```

**Si le temps manque**, l'ordre est : preflight + 3 répétitions (ne pas perdre
la démo), e2e boutique (prouver que l'achat fonctionne), puis le reste. Tout ce
qui n'est pas fait doit être **écrit** dans ce fichier ou dans
`docs/GOUVERNANCE.md` : une limite écrite ne coûte pas de point, une limite
découverte en Q&A en coûte.
