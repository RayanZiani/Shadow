# TODO — ce qui reste à faire

> État au 20 octobre 2026. Le MVP est fonctionnel et mesuré (148 tests verts,
> chaîne P0 en 6,6-7,2 s, 18 services `Up`).
> **Mise à jour du 20/10 (soir)** : les nouvelles règles du jury changent le
> périmètre — boutique en base, front vitrine, back d'administration, paiement
> réel avec historique, RAG, ingestion SIEM. **Livré** : voir §0.5. Ce qui
> reste est listé au même endroit, avec un critère d'acceptation.
> Ce fichier liste **uniquement ce qui n'est pas fait**, avec un critère
> d'acceptation vérifiable pour chaque ligne.

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
| N5 | SI **invulnérable**, aucune faille volontaire | **livré (1ʳᵉ passe)** | §0.5.1 : 5 failles réelles fermées. Audit complet reste à faire |
| N6 | **IA** : RAG, surveillance des alertes SIEM, remédiation autonome | **livré (socle)** | `services/rag-assistant`, `POST /alert` + Alertmanager → playbook |

### 0.5.1 Failles réelles fermées (N5)

Ces cinq points n'étaient pas des « failles de démonstration » : c'étaient des
trous qu'un jury CYBER exploite en quelques minutes.

| Faille | Exploit avant correction | Correction |
|---|---|---|
| `/auth/login` de `auth-service` acceptait **n'importe quel** `api_secret` | un JWT valide pour tout identifiant connu | vérification SHA-256 + `MERCHANT_SECRETS`, fail-closed 503 |
| `/admin/*` de `payment-api` et du WAF **ouverts** | quarantaine de 25 outputs, anneau à 64, déception désarmée | `SERVICE_TOKEN` obligatoire, comparaison à temps constant |
| `/incident` et `/state/report` **ouverts** | playbook déclenché par n'importe qui (déni de service) | idem |
| **PIN `1234` codé en dur** | terminal ouvert avec une valeur publique | `TERMINAL_PIN` généré par `tools/init_env.py`, blocage après 5 échecs |
| **Anneau non borné** sur `/tx/submit` | 100 000 leurres = saturation CPU | plafond à 64 leurres |
| `/ingest` du détecteur **ouvert** | évasion : diluer la fenêtre pour empêcher la détection | `SERVICE_TOKEN` obligatoire |

- [x] **Audit de sécurité systématique** — M
  `docs/SECURITE.md` : 6 failles fermées (chacune avec son test), mesures par
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

- [ ] **Test de bout en bout de l'achat sous Docker** — S
  `tools/e2e_shop.py` : catalogue → panier → paiement réel → commande `paid`
  → historique, en une commande, avec code de sortie.
  **Acceptation** : `chain_complete: true`, commande visible dans
  `/admin/orders`, stock décrémenté.

- [ ] **Vérifier la persistance PostgreSQL au redémarrage** — S
  `docker compose restart shop-api db-postgres` puis relire les commandes.
  **Acceptation** : l'historique et le stock sont identiques après
  redémarrage ; aucune commande perdue.

- [ ] **Test de concurrence sur le stock** — S
  Deux checkouts simultanés sur le dernier article.
  **Acceptation** : une seule commande `paid`, l'autre en `409`, stock jamais
  négatif. (Le code le fait ; il manque la mesure sous charge.)

- [ ] **Migration du registre d'outputs vers PostgreSQL** — L
  Toujours en mémoire (`payment-api`). Voir §1.3 : c'est la même base, la même
  transaction, et cela supprime le dernier « redémarrage destructif ».

- [ ] **Images produit** — S
  `image_url` existe en base mais aucun fichier n'est servi.
  **Acceptation** : un article avec image s'affiche dans la vitrine.

### 0.5.3 Ce qui reste sur l'IA (N6)

- [ ] **Faire tourner Ollama sur le PC IA** — M
  `ollama pull llama3.1:8b` puis `OLLAMA_URL=http://<pc-ia>:11434`.
  **Acceptation** : `/ask` renvoie `engine: ollama:llama3.1:8b` et une réponse
  rédigée citant `docs/RAPPORT.md`.

- [ ] **Brancher les logs (Loki) sur le SIEM** — M
  Aujourd'hui Alertmanager pousse les **alertes Prometheus**. Les **logs**
  (`loki`/`promtail`) ne sont toujours pas configurés (§2.2).
  **Acceptation** : une erreur de `payment-api` déclenche une alerte Loki →
  Alertmanager → `POST /alert` → playbook.

- [ ] **Corrélation multi-alertes** — M
  Une panne réelle produit 3 à 5 alertes en cascade. Aujourd'hui la première
  ouvre l'incident, les suivantes sont ignorées pendant le verrou.
  **Acceptation** : un incident unique est créé, avec la liste des alertes
  corrélées dans `detail.alerts`.

- [ ] **Scénario de démonstration « attaque sur la boutique »** — M
  Le jury CYBER attaque le SI : il faut un scénario qui montre l'attaque sur
  `shop-api` (panier forgé, quantité négative, checkout sans stock, rejeu de
  paiement), la détection et la remédiation.
  **Acceptation** : `tools/attack.py shop` → 4 attaques, toutes refusées
  proprement (400/409), aucune commande créée, service toujours vivant.

---

## 0. Avant la soutenance — bloquant, à faire la veille

Ces tâches ne sont pas du développement : sans elles, la démo peut échouer.

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

### 1.3 Registre persistant (PostgreSQL) — L

**Pourquoi** : `OUTPUTS` est en mémoire. Un redémarrage de `payment-api` en
pleine démo efface le registre, donc la transaction de preuve.

**Où** : `services/payment-api/main.py`, `docker-compose.yml`.

- table `outputs(id, age_days, one_time_public_key, tx_public_key,
  encrypted_amount, internal, spent_at)` ;
- table `key_images(key_image PRIMARY KEY, spent_at)` ;
- migration au démarrage (`CREATE TABLE IF NOT EXISTS`) ;
- `db-postgres` est déjà dans la stack et `DATABASE_URL` est déjà passé.

**Acceptation** :
- `docker compose restart payment-api` conserve les outputs et les key images ;
- un rejeu après redémarrage renvoie toujours 409 ;
- les 10 tests de `test_payment_api.py` passent avec un backend PostgreSQL
  (fixture de test sur une base éphémère).

**Effort réel** : la logique métier ne change pas, seul le stockage. Attention à
`_replenish_pool()` qui parcourt tous les outputs : passer en `COUNT(*)`.

### 1.4 RAG Ollama — L

**Pourquoi** : le sujet demande un assistant RAG local (Ollama + LangChain,
modèle ≤ 8 Md de paramètres). Le pilier IA/DATA n'est couvert que par le ML.

**Où** : `services/rag-assistant/` (à créer).

- indexer `docs/*.md` + `README.md` dans une base vectorielle légère
  (Chroma ou FAISS) ;
- modèle `llama3.1:8b` ou `mistral:7b` via Ollama ;
- endpoint `POST /ask` avec les sources citées ;
- quadrant « Assistant » dans le dashboard (optionnel).

**Acceptation** :
- `curl -X POST localhost:8091/ask -d '{"q":"combien de temps prend la reprise ?"}'`
  répond « 6,6 à 7,2 s » **en citant** `docs/RAPPORT.md` ;
- l'indexation se fait au build de l'image, jamais au runtime ;
- le service est `optional` dans le compose : son absence ne casse rien.

---

## 2. Priorité moyenne — qualité et crédibilité

### 2.1 Métriques PostgreSQL — S

`db-postgres` n'est pas scrapé : le service tourne mais n'expose aucune métrique
(aucun `postgres_exporter`). PostgreSQL n'est utilisé par aucun service
aujourd'hui (le registre est en mémoire), donc ce n'est pas urgent — mais si
1.3 est fait, ces métriques deviennent utiles.

**Acceptation** : `prometheus-community/postgres_exporter` ajouté, cible `up`,
dashboard Grafana avec connexions et taille de base.

### 2.2 Loki / Promtail non configurés — M

Les conteneurs démarrent mais ne collectent rien (aucune config de scrape de
logs). Deux options : configurer `promtail` sur les logs Docker, ou retirer les
deux services du compose pour ne pas afficher du vide.

**Acceptation** : les logs de `payment-api` sont visibles dans Grafana Explore.

### 2.3 Test de charge — S

Aucune mesure de tenue en charge. La rafale (`attack.py burst`) fait 9,7 tx/s,
ce qui n'est pas une mesure de saturation.

**Où** : `tools/load_test.py` (à créer), avec `httpx` + `asyncio`.

**Acceptation** : courbe débit/latence pour 1, 5, 10, 20 clients concurrents,
publiée dans le dossier technique. Identifier le point de rupture.

### 2.4 Chaos engineering formalisé — M

Le sujet mentionne « Chaos engineering : injection de défaillances et recette ».
Aujourd'hui les pannes sont testées **à la main** (couper un service, débrancher
l'ESP32). Rien n'est automatisé.

**Où** : `tools/chaos.py` (à créer).

Scénarios à couvrir :
- arrêt de `ml-detector` → le paiement doit continuer ;
- arrêt de `remediation-engine` → le dashboard doit afficher SSE KO, sans crash ;
- arrêt de `monero-crypto-core` → 503 explicite, pas de 500 ;
- coupure du pont hardware → la remédiation continue ;
- redémarrage de `payment-api` → registre conservé (dépend de 1.3).

**Acceptation** : chaque scénario produit un code de sortie et une ligne de
rapport ; intégré à `tools/run_tests.sh` derrière un flag `--chaos`.

### 2.5 Le quadrant « console Ansible » du dashboard est pauvre — S

Il affiche `ansible_rc` et la durée, mais pas la sortie du playbook, alors que
`/history` la contient (`ansible_stdout_tail`). Pendant le clip, voir les tâches
défiler est un argument visuel fort.

**Où** : `front-dashboard/src/App.jsx`.

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

### 3.3 Recette en 6 tests du `hardware/README.md` — S

Les 6 tests sont écrits mais n'ont pas été exécutés sur matériel.

---

## 4. Dette technique assumée

| Élément | Pourquoi c'est acceptable | Quand s'en occuper |
|---|---|---|
| `services/ml-detector/baseline.jsonl` non versionné | propre à chaque poste, reproductible en 7 min | jamais (par conception) |
| `tools/fake_ansible.py` et `local-dev/crypto_shim.py` | marqués non livrables, utiles pour un poste sans Docker | garder |
| `loki`/`promtail` sans config | ils démarrent, ne nuisent pas | cf. 2.2 |
| `ansible.cfg` absent | Ansible fonctionne sans | si on ajoute des rôles |
| Pas de CI (GitHub Actions) | le projet est un rendu, pas un produit | si le dépôt devient public |
| Pas de `pre-commit` | 104 tests suffisent | optionnel |

---

## 5. Ce qui est explicitement hors périmètre

Ne pas perdre de temps dessus, c'est un choix documenté :

- **Monero réel** (mainnet, nœud, sync) : le sujet parle d'un SI fictif.
- **RGPD** : aucune donnée personnelle traitée (montants et clés uniquement).
- **Homologation / pen-test formel** : hors périmètre étudiant.
- **Plan de formation, cellule de crise** : hors périmètre technique.
- **MFA** : noté `NON COUVERT` dans le dossier de gouvernance ; le PIN est la
  surface d'attaque volontairement faible qui alimente le ML.
- **WAF type ModSecurity/CRS** : nous avons écrit un proxy de déception sur
  mesure, ce qui est plus démontrable qu'une image OWASP non configurée.

---

## 6. Ordre d'exécution recommandé

```
Fait        0.5 nouvelles règles du jury           ← boutique, RAG, SIEM, durcissement
            (reste : e2e boutique, audit sécurité, Ollama réel, logs Loki)
Semaine 1   §0 (recalibrage + 3 répétitions)       ← bloquant, 1 h
            0.5.2 e2e boutique + concurrence       ← ½ j, prouve que l'achat marche
            1.2 AppRole Vault                      ← ½ j, gain immédiat en Q&A
Semaine 2   1.3 registre PostgreSQL                 ← 2 j, supprime un risque de démo
            0.5.1 audit de sécurité                 ← 1 j, « SI invulnérable » se prouve
Semaine 3   0.5.3 Ollama + logs Loki                ← 2 j, complète le pilier IA/DATA
            1.1 Bulletproofs                        ← 3-5 j, seule faille crypto
Semaine 4   §3 objet physique                       ← 1 j, non bloquant
            Passe finale : §0 + 0.5                 ← obligatoire la veille
```

**Si le temps manque** : §0, puis 0.5.2 (e2e boutique), puis 0.5.1 (audit
sécurité). Ce sont les trois lignes qui coûtent le moins et qui rapportent le
plus : ne pas perdre la démo, prouver que l'achat fonctionne, prouver que le SI
n'a pas de trou connu.
