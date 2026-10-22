# Règles du jury du 20/10 — six exigences, preuves et limites

> Réponse aux six exigences **N1–N6** ajoutées par le jury du 20/10 :
> site vitrine connecté à une base, front qui présente les articles, back
> d'administration, paiement réel avec stockage et historique, SI sans faille
> volontaire, IA (RAG + alertes SIEM + remédiation autonome).
>
> Chaque exigence dit **ce qui est livré**, **ce qui est prouvé par un test**,
> **ce qui exige la pile Docker** (à passer sur le PC hôte) et **ce qui reste
> ouvert**. Une limite écrite ne coûte pas de point ; une limite découverte en
> Q&A en coûte.
>
> **Chiffres** : **221 tests** (197 pytest + 5 script + 9 Rust + 10 HTTP) ·
> **19 services** · chaîne P0 **6,6 / 7,1 / 7,2 s** · RAG **18 documents /
> ~710 passages** · bundle dashboard **206,82 kB** · **PIN généré**, jamais une
> constante publique.

---

## 1. Vue d'ensemble

| # | Exigence du 20/10 | Livré | Preuve principale |
|---|---|---|---|
| N1 | SI de paiement comme un **site vitrine connecté à une base** | `services/shop-api` (8087), PostgreSQL `db-postgres`, `Storefront.jsx` | `GET /products`, 6 articles en base ; tests `shop-api` |
| N2 | **front qui présente des articles en base** | `Storefront.jsx`, `ProductImage.jsx`, images `/static/products/*.svg` | `GET /products` + 3 tests d'images |
| N3 | **back d'administration** | `AdminShop.jsx`, `/admin/products`, `/admin/orders`, garde du dernier admin | tests `shop-api` + `backoffice` |
| N4 | **paiement en ligne avec stockage et historique** | `POST /checkout` → `/tx/pay` bLSAG → `orders` / `payments` / `payment_events` | 10 tests `shop-api` + 3 tests `payment-api` |
| N5 | **SI invulnérable, aucune faille volontaire** | `docs/SECURITE.md` (F1–F12, 4 bugs critiques), `tools/attack.py shop`, PIN généré | 6 familles d'attaques + `preflight.py` |
| N6 | **IA : RAG + surveillance SIEM + remédiation autonome** | `services/rag-assistant`, `POST /alert`, Alertmanager, corrélation multi-alertes, playbook | 4 tests RAG + 7 tests d'ingestion SIEM |

**Comment lire** : « test » = exécutable sans Docker, sauf mention contraire ;
« PC hôte » = la pile de 19 services doit tourner ; « ouvert » = assumé et écrit
dans `docs/SECURITE.md`, `docs/INSTALLATION-LOCAL.md` §7 ou `TODO.md`.

---

## 2. N1 — un site vitrine connecté à une base

### Livré

- `services/shop-api` sur le port **8087**, base **PostgreSQL** (`db-postgres`,
  base `shadow_settle`). En test, le même SQL tourne sur SQLite en mémoire.
- Le front `front-dashboard/src/shop/Storefront.jsx` lit le catalogue par
  `GET /products` ; le panier est persistant (`carts`, `cart_items`).
- Le schéma relie `products` et `carts` par `cart_items`, puis `orders` →
  `order_items` → `payments` → `payment_events`.
- Aucune solution du commerce (Prestashop ou autre) : le code est écrit ici,
  donc la surface d'attaque et le modèle de données sont maîtrisés.

### Prouvé par un test

| Test | Ce qu'il prouve |
|---|---|
| `test_le_catalogue_est_en_base_et_non_code_en_dur` | les articles viennent de la base, pas d'une liste JavaScript |
| `test_healthz_declare_le_moteur_de_base` | `/healthz` publie `database: sqlite` ou `postgresql` |
| `test_postgres_dialect.py` (4 tests) | le SQL est compatible PostgreSQL (booléens `TRUE`/`FALSE`, schéma) |

Les 4 tests de dialecte sont **statiques** : ils prouvent le SQL, pas le
serveur. La preuve PostgreSQL réelle est la pile Docker ci-dessous.

### À passer sur le PC hôte (pile Docker)

```powershell
docker compose up -d db-postgres shop-api front-dashboard
curl.exe -s http://localhost:8087/healthz
# attendu : "status": "ok", "database": "postgresql"

curl.exe -s http://localhost:8087/products | python -m json.tool
# attendu : 6 articles, chacun avec image_url

python tools/preflight.py
# attendu : "service P1 : shop-api (/healthz + /products)" OK
```

Puis recharger la page : les articles doivent être là. Redémarrer `shop-api`
(`docker compose restart shop-api`) et vérifier que le catalogue et l'historique
sont toujours là : c'est la preuve que PostgreSQL, et non la mémoire du
processus, est la source de vérité.

### Reste ouvert

| Point | Pourquoi c'est acceptable | Où c'est dit |
|---|---|---|
| PostgreSQL **mono-nœud** | démonstration sur un seul PC hôte ; pas de bascule | ce document |
| pas de sauvegarde / PITR | le volume `pg-data` survit à un redémarrage, pas à une perte de disque | ce document |
| pas d'outil de migration | schéma créé au démarrage (`CREATE TABLE IF NOT EXISTS`) | `services/shop-api/db.py` |
| SQLite en test | un test qui exige un serveur ne tourne pas partout ; le jour J, c'est PostgreSQL | `services/shop-api/README.md` §4 |

---

## 3. N2 — un front qui présente les articles en base

### Livré

- `GET /products` renvoie les **articles actifs** (6 au seed) avec `sku`, `name`,
  `description`, `price_cents`, `stock`, `active`, `image_url`.
- Les images sont des **SVG locaux** servis par le montage `/static` :
  `services/shop-api/static/products/*.svg` → `GET /static/products/<SKU>.svg`.
- `front-dashboard/src/shop/ProductImage.jsx` préfixe le chemin relatif par
  l'URL de l'API ; aucune URL absolue n'est stockée en base.
- Le front affiche le stock, le prix et un état « rupture » quand l'article est
  épuisé ; un article retiré (inactif) disparaît du catalogue.

### Prouvé par un test

| Test | Ce qu'il prouve |
|---|---|
| `test_les_images_des_six_articles_sont_servies` | les 6 SVG répondent `200 image/svg+xml` |
| `test_le_catalogue_expose_une_image_pour_chaque_article` | chaque article a un `image_url` non vide |
| `test_le_montage_statique_survit_a_un_dossier_absent` | le service démarre même sans `static/` (images `404`, pas de crash) |

### À passer sur le PC hôte (pile Docker)

```powershell
curl.exe -s http://localhost:8087/products | python -m json.tool
curl.exe -s -o NUL -w "%{http_code} %{content_type}`n" `
  http://localhost:8087/static/products/TERM-01.svg
# attendu : 200 image/svg+xml

cd front-dashboard
npm run build
# attendu : bundle ~206,82 kB
```

### Reste ouvert

| Point | Pourquoi c'est acceptable | Où c'est dit |
|---|---|---|
| images **SVG illustratives**, pas des photos produit | un SI fictif n'a pas de catalogue photo ; le contrat `image_url` est réel | `services/shop-api/README.md` §2 |
| pas de pagination / recherche | 6 articles, une seule page | ce document |
| pas de CDN / cache HTTP | démonstration locale | ce document |
| accessibilité non auditée | hors périmètre de la soutenance | ce document |

---

## 4. N3 — un back d'administration

### Livré

- `front-dashboard/src/shop/AdminShop.jsx`, accessible par
  **« Habilitations → Boutique »**.
- Routes : `GET/POST /admin/products`, `PATCH/DELETE /admin/products/{id}`,
  `GET /admin/orders` (100 dernières commandes avec leurs lignes).
- `DELETE` **désactive** l'article, il ne le supprime pas : l'historique doit
  rester lisible.
- L'administration exige une **session back-office validée auprès du
  back-office** et le rôle `admin`. Le jeton de service n'est jamais envoyé au
  navigateur.
- Le **dernier administrateur connectable** ne peut être ni désactivé ni
  rétrogradé, ni privé de son visage (garde testée, y compris en concurrence).

### Prouvé par un test

| Test | Ce qu'il prouve |
|---|---|
| `test_creation_et_modification_dun_article` | création, `PATCH`, stock et prix mis à jour |
| `test_ladministration_exige_une_session_admin` | sans session : refus |
| `test_un_service_token_invalide_est_refuse` | le jeton de service n'ouvre pas l'administration à la place d'un admin |
| `test_le_dernier_admin_ne_peut_pas_etre_desactive` | garde du dernier admin |
| `test_deux_patch_concurrents_un_seul_admin_connectable_survit` | la garde tient sous concurrence |
| `test_le_dernier_admin_avec_visage_ne_peut_pas_etre_desactive` | un admin sans visage ne compte pas comme connectable |

### À passer sur le PC hôte (pile Docker)

```powershell
# 1. se connecter au back-office (identifiant + visage + mot de passe)
# 2. « Habilitations » → « Boutique »
# 3. créer un article, modifier son stock, le désactiver
# 4. vérifier qu'il disparaît de la vitrine, mais reste dans /admin/products

curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:8087/admin/orders
# attendu sans session : 401 (ou 503 si le back-office est arrêté)
```

### Reste ouvert

| Point | Pourquoi c'est acceptable | Où c'est dit |
|---|---|---|
| pas de **séparation des tâches** (4 yeux) | un seul rôle admin pour la boutique | ce document |
| pas d'export d'audit | le journal du back-office trace les événements | `services/backoffice` |
| pas de rôles fins côté boutique | `admin` ou rien ; le back-office gère `operator` / `auditor` | `docs/SECURITE.md` §2 |
| pas de workflow d'approbation | démonstration, pas production | ce document |

---

## 5. N4 — paiement en ligne, stockage et historique

### Livré

Le parcours complet, en une route :

```
POST /checkout
  ├─ réclamation atomique du panier (UPDATE ... WHERE checked_out_at IS NULL)
  ├─ réservation du stock (WHERE stock >= ?), sinon 409
  ├─ commande + lignes + paiement `pending` + événement `created`
  ├─ POST payment-api /tx/pay  (montant exact, jamais celui du client)
  │    └─ output du montant exact → signature d'anneau bLSAG → key image
  ├─ succès : `paid` + key image + ring size + événement `paid`
  └─ échec  : stock restitué + `failed` + motif + panier relâché
```

- Tables : `orders`, `order_items`, `payments`, `payment_events`.
- Le **prix est recopié** dans `order_items` au moment de l'achat : un
  changement de tarif ne réécrit pas l'historique.
- L'historique est protégé par un **capability token** `customer_ref`
  (192 bits, `token_urlsafe(24)`) : sans lui, `GET /orders/{ref}` répond `404`,
  pas `403`.
- Le paiement est **idempotent par `reference`** : deux appels avec la même
  référence ne paient qu'une fois ; une référence réutilisée avec un autre
  montant est refusée.
- Une commande `pending` orpheline est réparée par un **balayage** (au démarrage
  et toutes les 30 s) : elle passe en `failed` et le stock est restitué, une
  seule fois.

### Prouvé par un test

| Test | Ce qu'il prouve |
|---|---|
| `test_achat_nominal_stocke_la_commande_et_le_paiement` | commande, paiement et événements persistés |
| `test_le_paiement_refuse_restitue_le_stock` | un refus ne perd pas de stock |
| `test_le_paiement_injoignable_restitue_le_stock` | une panne réseau non plus |
| `test_deux_checkouts_sur_le_meme_panier` | le double-clic ne crée pas deux commandes |
| `test_achats_simultanes_ne_survendent_pas` | pas de survente sous concurrence |
| `test_un_prix_envoye_par_le_client_est_ignore` | le total vient de la base |
| `test_lhistorique_exige_le_customer_ref` | pas d'historique sans capability token |
| `test_un_token_non_ascii_ne_fuit_pas_lexistence` | pas d'oracle 500/404 |
| `test_le_balayage_expire_une_commande_pending_et_restitue_le_stock` | réparation des `pending` |
| `test_la_reconciliation_ne_restitue_pas_deux_fois_un_paiement_reussi` | pas de double restitution |
| `test_deux_appels_avec_la_meme_reference_ne_paient_qu_une_fois` | idempotence par `reference` |
| `test_une_double_depense_concurrente_est_refusee` | key image réservée avant le premier `await` |
| `test_la_depense_est_atomique` | `UPDATE outputs` + `INSERT key_images` dans une transaction |

### À passer sur le PC hôte (pile Docker)

```powershell
# 1. acheter dans la vitrine : la commande doit être `paid`
# 2. ouvrir l'historique : la key image et le ring size sont affichés
# 3. redémarrer shop-api, recharger : l'historique est toujours là
docker compose restart shop-api

# 4. panne réelle : arrêter le noyau, acheter, vérifier `failed` + stock rendu
docker compose stop monero-crypto-core
# acheter → commande failed, stock restitué, paiement refusé dans les logs
docker compose start monero-crypto-core
docker compose ps monero-crypto-core payment-api   # attendre "healthy"
curl.exe -s http://localhost:8080/tx/auto-probe/status   # attendre "ok": true
python tools/preflight.py
```

### Reste ouvert

| Point | Risque | Pourquoi c'est acceptable | Où c'est dit |
|---|---|---|---|
| **pas de Bulletproofs** | un montant négatif modulo l passe l'équilibre | il faut un attaquant authentifié qui forge ses engagements | `/healthz`, `TODO.md` §1.1 |
| pas de règlement on-chain réel | le noyau est un modèle Monero simplifié | la propriété prouvée est la signature d'anneau et la key image | `docs/RAPPORT.md` |
| pas de remboursement | une commande `failed` rend le stock, pas l'argent | SI fictif, montants en centimes | `services/shop-api/README.md` §6 |
| pas de réconciliation bancaire | hors périmètre | la réconciliation interne `paid`/`pending` est faite | `services/shop-api/README.md` §2 |
| pas de TLS | trafic local | `localhost` uniquement ; production : TLS + HSTS | `docs/SECURITE.md` O1 |

---

## 6. N5 — un SI sans faille volontaire

### Livré

- `docs/SECURITE.md` : **12 failles F1–F12** trouvées et fermées, avec pour
  chacune l'exploit d'avant, la correction et le test.
- **4 bugs critiques** fermés par l'audit : boutique inutilisable sur PostgreSQL
  (C1), double dépense concurrente (F8), double débit par double-clic (F9),
  oracle d'existence 404/500 (F10).
- `tools/attack.py shop` : **6 familles** d'attaques sur la vitrine et son
  panier, en une seconde, avec code de sortie.
- Le **PIN du terminal est généré** (`TERMINAL_PIN`), jamais une constante
  publique ; 5 refus → verrouillage 60 s ; l'indice a disparu de l'interface.
- Les endpoints d'administration sont **fail-closed** : sans `SERVICE_TOKEN`,
  ils répondent `503` ; avec un mauvais jeton, `401`.

### Prouvé par un test

| Faille | Test |
|---|---|
| F1 login marchand | `test_mauvais_secret_refuse`, `test_sans_table_de_secrets_le_service_refuse_de_signer` |
| F2 `/admin/*` ouverts | `test_les_endpoints_admin_sont_fermes_sans_jeton` |
| F3 `/incident` ouvert | `test_le_siem_exige_le_jeton_de_service` |
| F4 PIN public | `test_le_pin_par_defaut_ne_fonctionne_plus`, `test_pin_refuse_compte_et_refuse` |
| F5 anneau non borné | `test_anneau_trop_grand_refuse`, `test_65_leurres_sont_refuses_avant_toute_recherche` |
| F6 `/ingest` ouvert | `test_ingest_exige_le_jeton_de_service` |
| F7 entrées non strictes | `test_une_quantite_en_chaine_est_refusee`, `test_un_sku_malforme_est_refuse` |
| F8 double dépense concurrente | `test_une_double_depense_concurrente_est_refusee` |
| F9 double débit | `test_deux_checkouts_sur_le_meme_panier` |
| F10 oracle 404 | `test_un_token_non_ascii_ne_fuit_pas_lexistence` |
| F11 PIN non-ASCII | `test_le_pin_non_ascii_est_compte_comme_un_echec` |
| F12 alerte SIEM morte | `tools/check_compose.py` (chaîne alerte → actionnable) |

### À passer sur le PC hôte (pile Docker)

```powershell
# 1. les six familles d'attaques boutique
python tools/attack.py shop

# 2. le pré-vol (secrets, PIN, services, dépôt)
python tools/preflight.py

# 3. aucun secret dans le CONTENU des fichiers suivis
git grep -n -E "hvs\.|BEGIN [A-Z ]*PRIVATE KEY" -- .
# attendu : aucune ligne (code de sortie 1)

# 4. les endpoints d'administration sont fermés
curl.exe -s -o NUL -w "%{http_code}`n" -X POST http://localhost:8080/admin/quarantine-outputs
curl.exe -s -o NUL -w "%{http_code}`n" -X POST http://localhost:8085/admin/reset
curl.exe -s -o NUL -w "%{http_code}`n" -X POST http://localhost:8084/incident -d "{}"
# attendu : 401 ou 503
```

### Reste ouvert

| Point | Risque | Pourquoi c'est acceptable | Où c'est dit |
|---|---|---|---|
| **pas de TLS** | écoute du trafic local | `localhost` ; production : TLS + HSTS | `SECURITE.md` O1 |
| **pas de vivacité** | une photo imprimée passe le facteur visage | le mot de passe reste exigé ; publié dans `/healthz` | `SECURITE.md` O2 |
| **pas de Bulletproofs** | montant négatif modulo l | attaquant authentifié nécessaire | `SECURITE.md` O3 |
| **pas de WAF générique** | pas de couverture OWASP top-10 | validation Pydantic par service ; le WAF maison fait de la déception | `SECURITE.md` O6 |
| **AppRole Vault non provisionné** | le playbook utilise `VAULT_TOKEN` | jeton de service, pas de privilège root | `INSTALLATION-LOCAL.md` §7 |
| **rate-limit `/tx/pay` en mémoire** | désactivé par défaut, par processus | la démonstration est mono-instance | `services/payment-api/main.py` |
| **pas de revue d'accès périodique** | un compte oublié reste actif | journal d'audit + sessions révocables | `SECURITE.md` O7 |

**Ce qu'on ne dit pas** : « invulnérable ». On dit : « 12 failles fermées,
6 familles d'attaques rejouables en une commande, et les limites écrites. »
C'est vérifiable ; « invulnérable » ne l'est pas.

---

## 7. N6 — IA : RAG, surveillance SIEM, remédiation autonome

### Livré

**RAG (`services/rag-assistant`, 8091).**

- Index **BM25** construit à la construction de l'image sur `docs/`,
  `README.md` et `TODO.md` : **18 documents, ~710 passages** pour l'image de
  démonstration (`/healthz` fait foi après un rebuild).
- `POST /ask` renvoie une réponse **avec ses sources** (fichier, section, score)
  et le **moteur** utilisé : `extractive` ou `ollama:<modèle>`.
- Si Ollama est muet, le service répond en **extractif** — les phrases du
  dossier, citées — et le dit dans `/healthz` (`generation.ollama:
  unreachable`). Il n'invente pas.
- Le quadrant **« Assistant RAG — dossier technique »** du dashboard rend le
  dispositif visible à l'écran, pas seulement en `curl`.

**Surveillance SIEM et remédiation (`POST /alert`).**

- Prometheus évalue **24 règles** ; Loki en ajoute **4** sur les logs.
- Alertmanager pousse les alertes à `remediation-engine` (`POST /alert`).
- La liste des alertes **actionnables** est fermée (`ACTIONABLE_ALERTS`) : une
  alerte de confort (bandeau LED, compte bloqué) est journalisée sans ouvrir
  d'incident.
- **Corrélation multi-alertes** : une panne réelle produit 3 à 5 alertes en
  cascade ; celles reçues dans les **10 s** suivant l'ouverture sont rattachées
  au **même** incident (`detail.alerts`), pas cinq incidents distincts.
- Le moteur réutilise **exactement** le chemin du détecteur ML : même verrou,
  même playbook Ansible, même preuve de reprise.

### Prouvé par un test

| Test | Ce qu'il prouve |
|---|---|
| `test_lapi_repond_avec_sources_et_moteur` | `/ask` renvoie réponse, sources et moteur |
| `test_le_mode_extractif_cite_ses_sources` | sans Ollama, la réponse cite le dossier |
| `test_un_tableau_seul_repond_a_une_question_de_duree` | une ligne de tableau reste retrouvable |
| `test_la_question_de_reference_retrouve_le_rapport` | la question de démonstration trouve `docs/RAPPORT.md` |
| `test_une_alerte_actionnable_ouvre_un_incident` | `PaiementRefuse` déclenche le playbook |
| `test_une_alerte_de_confort_est_journalisee_sans_agir` | une alerte non actionnable n'agit pas |
| `test_une_rafale_dalertes_produit_un_seul_incident` | corrélation : un seul incident |
| `test_une_alerte_tardive_est_rattachee_a_lincident_courant` | fenêtre de 10 s |
| `test_une_alerte_hors_fenetre_est_ignoree` | pas d'extension indéfinie |
| `test_deux_alertes_du_meme_payload_ne_lancent_quun_playbook` | verrou atomique |
| `test_le_siem_exige_le_jeton_de_service` | `/alert` n'est pas ouvert |

### À passer sur le PC hôte (pile Docker)

```powershell
# 1. RAG : index et réponse sourcée
curl.exe -s http://localhost:8091/healthz
# attendu : "documents": 18, "chunks" ~710, generation.ollama reachable

$q = @{ q = "comment le stock est-il réservé ?" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8091/ask `
  -ContentType "application/json" -Body $q | ConvertTo-Json -Depth 4
# attendu : answer, sources[], engine = extractive ou ollama:...

# 2. SIEM : arrêter le noyau, acheter, injecter l'alerte, regarder le playbook
docker compose stop monero-crypto-core
# acheter dans la vitrine → commande failed, log "paiement refusé"

$tok = ((Select-String -Path .env -Pattern '^SERVICE_TOKEN=').Line -split '=',2)[1]
$body = @{
  status = "firing"
  alerts = @(@{
    labels      = @{ alertname = "PaiementRefuse"; severity = "critical" }
    annotations = @{ summary = "paiement refusé par le registre" }
  })
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://localhost:8084/alert `
  -Headers @{ "X-Service-Token" = $tok } `
  -ContentType "application/json" -Body $body
Remove-Variable tok

# 3. remettre le noyau et vérifier
docker compose start monero-crypto-core
python tools/preflight.py
```

Alertmanager fait la même chose automatiquement après l'évaluation de la règle
Loki (30 s) ; l'injection manuelle ne sert qu'à rendre la démonstration
déterministe. Le playbook est le même que celui du détecteur ML.

### Reste ouvert

| Point | Risque | Pourquoi c'est acceptable | Où c'est dit |
|---|---|---|---|
| **Ollama non branché** | réponses moins fluides | le mode `extractive` cite les phrases du dossier ; `/healthz` le dit | `INSTALLATION-LOCAL.md` §7 |
| pas de benchmark RAG | qualité non mesurée | 4 questions de référence testées ; les sources sont vérifiables | ce document |
| fenêtre de corrélation **10 s** | une alerte tardive est ignorée | choix explicite : un incident ne s'étend pas indéfiniment | `services/remediation-engine/main.py` |
| pas de rollback automatique | un playbook qui échoue laisse `REMEDIATION_FAILED` | l'état terminal est toujours publié ; la trace est dans `/history` | `SECURITE.md` §2 bis |
| pas d'approbation humaine | l'IA agit seule | c'est l'exigence ; le périmètre est fermé aux alertes actionnables | ce document |
| pas d'évaluation formelle du LLM | hallucinations possibles en mode Ollama | le mode de repli de la démo est `extractive` | ce document |

---

## 8. Ce qui reste ouvert, regroupé

| # | Point | Effet | Où c'est écrit | Quand |
|---|---|---|---|---|
| O1 | **TLS absent** | trafic local lisible | `docs/SECURITE.md` | production |
| O2 | **vivacité absente** | une photo passe le facteur visage | `/healthz`, `SECURITE.md` | chantier dédié |
| O3 | **Bulletproofs absentes** | montant négatif modulo l | `TODO.md` §1.1 | hors démonstration |
| O4 | **AppRole Vault non provisionné** | playbook avec `VAULT_TOKEN` | `INSTALLATION-LOCAL.md` §7 | production |
| O5 | **PostgreSQL mono-nœud** | pas de bascule | ce document | production |
| O6 | **Ollama non branché** | RAG extractif | `INSTALLATION-LOCAL.md` §7 | optionnel |
| O7 | **remboursement absent** | stock rendu, pas l'argent | `services/shop-api/README.md` §6 | hors périmètre |
| O8 | **firmware non flashé** | E10, E11, E13–E16 non prouvées sur la carte | `hardware/led_bandeau_esp8266/README.md` | avant soutenance |
| O9 | **WAF générique absent** | pas de couverture OWASP top-10 | `SECURITE.md` O6 | production |
| O10 | **rate-limit `/tx/pay` en mémoire** | par processus, désactivé par défaut | `services/payment-api/main.py` | production |

---

## 9. Ce qu'on répond si le jury demande X

| Question | Réponse courte | Preuve |
|---|---|---|
| « Où sont les articles ? » | dans PostgreSQL, table `products` ; le front lit `GET /products` | `test_le_catalogue_est_en_base_et_non_code_en_dur` |
| « Le paiement est-il réel ? » | oui : `/tx/pay` émet un output du montant exact et le dépense par bLSAG ; la key image est stockée | `test_achat_nominal_stocke_la_commande_et_le_paiement` |
| « Et si PostgreSQL tombe ? » | la boutique passe en `degraded` / 503, `preflight.py` le voit, on redémarre `db-postgres` puis `shop-api` | `test_lhealthz_degrade_au_lieu_de_tomber` |
| « Le back est-il protégé ? » | session back-office + rôle `admin` ; le dernier admin ne peut pas être supprimé | `test_ladministration_exige_une_session_admin` |
| « Votre SI est-il invulnérable ? » | non ; 12 failles fermées, 6 familles rejouables, limites écrites | `docs/SECURITE.md` |
| « Le PIN est-il une valeur publique ? » | non : il est généré et transmis par `.env` ; `preflight.py` vérifie qu'il arrive au conteneur | `test_le_pin_par_defaut_ne_fonctionne_plus` |
| « Votre IA invente-t-elle ? » | elle cite ses sources ; sans Ollama, elle passe en extractif et le dit | `test_le_mode_extractif_cite_ses_sources` |
| « Une alerte déclenche-t-elle vraiment ? » | oui : `PaiementRefuse` → `/alert` → même playbook que le ML | `test_une_alerte_actionnable_ouvre_un_incident` |
| « Combien de tests ? » | 221 : 197 pytest, 5 script, 9 Rust, 10 HTTP | `.\tools\run_tests.ps1` |
| « Pourquoi pas Prestashop ? » | le SI est écrit ici : modèle de données, surface d'attaque et preuve maîtrisés ; pas de CMS à durcir | `services/shop-api/README.md` |
| « Pourquoi le firmware n'est pas flashé ? » | il compile (35,4 % RAM, 28,0 % flash) ; le flash et le rendu LED restent à prouver sur la carte | `docs/CONSIGNE-ADDITIONNELLE.md` §1 |
| « Qu'est-ce qui reste ouvert ? » | TLS, vivacité, Bulletproofs, AppRole, PostgreSQL mono-nœud, Ollama | §8 ci-dessus |
