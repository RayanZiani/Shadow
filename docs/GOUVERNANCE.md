# Dossier de gouvernance — RESILIENCE-ALPHA / SHADOW-SETTLE

> **Statut.** Ce document relie une analyse de risques (EBIOS RM), un plan de
> continuité et de reprise (PCA/PRA) et les exigences NIS2/DORA à
> l'**architecture réellement déployée** dans ce dépôt. Chaque mesure citée
> renvoie à un fichier ou un endpoint vérifiable. Les mesures non implémentées
> sont marquées **NON COUVERT** : c'est un choix de rédaction, pas un oubli.
>
> Ce qui rend ce dossier défendable en Q&A : il ne décrit pas un système idéal,
> il décrit celui qui tourne, avec ses limites assumées.

---

## 1. Périmètre

| Élément | Valeur |
|---|---|
| Système étudié | Plateforme de paiement fictive à confidentialité cryptographique |
| Mode de déploiement | `docker compose`, 19 services, un PC hôte |
| Périmètre technique | `services/*`, `front-dashboard`, `ansible/`, `hardware/` |
| Périmètre organisationnel | Un consortium d'étudiants, un PC attaquant, un PC hôte |
| Hors périmètre | Monero réel, réseau public, clients réels ; les données du back-office sont traitées au §8.4 |
| Méthode | EBIOS RM (ANSSI, 2018), 5 ateliers |
| Référentiels | NIS2 (directive UE 2022/2555), DORA (règlement UE 2022/2554) |

La démonstration dure 3 minutes et se joue **une fois**. Le risque le plus
coûteux n'est donc pas une compromission : c'est une **panne silencieuse** le
jour J. L'analyse ci-dessous le reflète.

---

## 2. Atelier 1 — Cadrage et biens essentiels

### 2.1 Valeurs métier (ce qui a de la valeur)

| # | Valeur métier | Description | Impact si dégradé |
|---|---|---|---|
| VM1 | **Confidentialité des montants** | Un montant ne doit être lisible que par le porteur et l'auditeur habilité | Fuite = perte de confiance, non-conformité |
| VM2 | **Intégrité des transactions** | Une transaction acceptée ne doit pas pouvoir être rejouée | Double dépense = perte financière directe |
| VM3 | **Disponibilité du paiement** | Un client doit pouvoir payer pendant et après un incident | Perte de service, rupture |
| VM4 | **Preuve de reprise** | Le système doit démontrer qu'il s'est rétabli seul | Perte de la démonstration (25 pts) |
| VM5 | **Traçabilité auditable** | Un auditeur doit pouvoir reconstituer les faits | Non-conformité DORA art. 19 |

### 2.2 Biens supports (composants réels)

| # | Bien support | Service / fichier | Dépend de |
|---|---|---|---|
| BS1 | Noyau cryptographique | `services/monero-crypto-core` (Rust) | — |
| BS2 | API de paiement | `services/payment-api` (port 8080) | BS1 |
| BS3 | Registre des outputs | Journal PostgreSQL + cache mémoire | BS2, BS14 |
| BS4 | Détection ML | `services/ml-detector` (8083) | — |
| BS5 | Moteur de remédiation | `services/remediation-engine` (8084) | Ansible, BS2 |
| BS6 | Playbook de remédiation | `ansible/remediation_playbook.yml` | BS5 |
| BS7 | Source de vérité d'état | `StateBus` dans BS5 | — |
| BS8 | Dashboard | `front-dashboard` (5173) | BS5, BS4, BS2 |
| BS9 | Terminal physique | `hardware/bridge` + ESP32 | BS5 |
| BS10 | Point d'entrée / déception | `services/waf` (8085) | BS2, honeypot |
| BS11 | Honeypot | `services/shadow-api` (8090) | — |
| BS12 | Gestion des secrets | `vault` (8200) | — |
| BS13 | Authentification | `services/auth-service` (8082) | BS12 |
| BS14 | Base de données | `db-postgres` — commandes, paiements, stock et registre d'outputs | — |
| BS15 | **Authentification forte du back-office** | `services/backoffice` (8086) + caméra USB | SQLite locale (`bo-data`) — **pas** BS14, voir la note sous le tableau ; navigateur du PC hôte |
| BS16 | **Bandeau LED d'état** | `hardware/led_bandeau_esp8266` + ESP8266 | WiFi du point d'accès, BS15 |
| BS17 | **Boutique en ligne** | `services/shop-api` (8087) : catalogue, paniers, commandes, paiements, stock | BS14, BS2, BS15 |
| BS18 | **Assistant documentaire (RAG)** | `services/rag-assistant` (8091) + index BM25 construit au build | Ollama sur le PC IA (optionnel, hors pile) |
| BS19 | **Chaîne de journaux** | `loki` (3100) + `promtail` (lecture seule du socket Docker) | — |
| BS20 | **Métriques PostgreSQL** | `postgres-exporter` (9187) | BS14 |

**Deux moteurs, deux usages.** PostgreSQL (BS14) porte la boutique et le
registre d'outputs, qui ont besoin de transactions et de concurrence. Le
back-office (BS15) garde une base **SQLite** locale (`/data/backoffice.db`,
volume `bo-data`) : quelques dizaines de lignes, aucun accès concurrent lourd,
et il doit pouvoir démarrer même sans PostgreSQL. La dépendance de BS15 est
donc son propre fichier SQLite, pas BS14 — écrire l'inverse serait faux.

### 2.3 Événements redoutés

| # | Événement redouté | Valeur métier affectée | Gravité |
|---|---|---|---|
| ER1 | Un montant est lu par un tiers non habilité | VM1 | 3 |
| ER2 | Une transaction est rejouée (double dépense) | VM2 | 4 |
| ER3 | Le paiement est indisponible pendant la démonstration | VM3, VM4 | 5 |
| ER4 | La remédiation ne se déclenche pas (panne silencieuse) | VM4 | 5 |
| ER5 | L'attaquant n'est pas contenu et continue de taper | VM3, VM5 | 3 |
| ER6 | L'auditabilité est perdue (aucune trace exploitable) | VM5 | 3 |
| ER7 | **Un tiers accède au back-office avec un identifiant ou un mot de passe volé** | VM1, VM5 | 4 |
| ER8 | **Un compte légitime est bloqué et la démonstration s'arrête** | VM3, VM4 | 3 |
| ER9 | **Un article est facturé moins cher que son prix catalogue, ou le stock devient négatif** | VM2, VM3 | 4 |
| ER10 | **L'historique d'achats d'un client est lu par un tiers** | VM1, VM5 | 3 |

---

## 3. Atelier 2 — Sources de risque

| # | Source | Motivation | Capacité | Pertinence ici |
|---|---|---|---|---|
| SR1 | Attaquant externe opportuniste | Financière | Moyenne | **Oui** — scénario principal de la démo |
| SR2 | Attaquant externe avancé | Financière / réputationnelle | Élevée | Partielle (pas d'APT réaliste sur 3 min) |
| SR3 | Erreur d'exploitation | Aucune | — | **Oui** — cause réelle de la majorité des pannes trouvées en recette |
| SR4 | Défaillance matérielle (PC, câble USB) | Aucune | — | **Oui** — l'objet physique peut tomber |
| SR5 | Compromission de la chaîne de build | Sabotage | Moyenne | Partielle (images non épinglées par digest) |

**Constat d'audit à assumer** : **12 failles réelles** (F1–F12) et **4 bugs
critiques** (C1, C2, C4, S1) ont été trouvés puis fermés — détail et tests dans
[`docs/SECURITE.md`](SECURITE.md). Aucun n'a été exploité pendant une
démonstration : les failles ont été trouvées par audit de code et par `curl`, les
bugs par la recette. Le risque dominant reste SR3 (erreur d'exploitation) et SR4
(dépendance matérielle) : c'est le vrai risque d'un système qui doit tenir
3 minutes devant un jury. Les **12 points moyens** relevés par l'audit restent
suivis dans `TODO.md` §0.6 ; ceux qui sont encore ouverts y sont écrits, pas
cachés ici.

---

## 4. Atelier 3 — Scénarios stratégiques

### SS1 — Empoisonnement des leurres (SR1)

Un attaquant qui veut casser la confidentialité d'un paiement peut tenter de
forcer l'anneau de signature à contenir des outputs qu'il contrôle. Il lit le
registre public (`GET /outputs`) et tire **uniformément**, alors qu'un wallet
honnête tire selon une gamma biaisée récents (`GET /decoys`).

- Chemin d'attaque : `POST /tx/submit` avec `decoy_ids` choisis.
- Détection : `decoy_median_age_days`, AUC 1,000 (§5.1).
- Réponse : quarantaine des outputs les plus anciens, durcissement de l'anneau.

### SS2 — Rejeu de transaction (SR1)

Rejouer une transaction déjà acceptée pour débiter deux fois.

- Chemin d'attaque : renvoyer une key image déjà dépensée.
- Détection : `key_image_rejection_ratio`.
- Réponse : rejet 409 **et** publication de la télémétrie (sinon l'attaque est
  invisible pour le ML — c'est un défaut corrigé, cf. `payment-api/main.py`).

### SS3 — Indisponibilité par saturation (SR1)

Rafale automatisée pour saturer le service et faire échouer la démo.

- Détection : `inter_arrival_median` (0,05 s en rafale contre 0,7 s en usage
  humain).
- Réponse : déception active (le trafic suspect part au honeypot), le client
  légitime continue de payer.

### SS4 — Panne silencieuse (SR3) — **le scénario le plus probable**

Un webhook pointe vers un nom d'hôte inexistant, une tâche Ansible échoue sous
`ignore_errors`, un seuil de calibration est périmé : le système a l'air sain
et ne détecte plus rien.

- Mesures : `/healthz` expose `last_webhook_error`, `runtime_threshold_ignored`,
  `crypto_core`, `crypto_last_error` ; `tools/e2e_demo.py` échoue bruyamment.

### SS5 — Perte de l'objet physique (SR4)

L'ESP32 est débranché en pleine démonstration.

- Réponse : le pont log et retente, la remédiation continue (aucun abonné SSE
  obligatoire). Analyse complète : `docs/hardware/CONCEPT-PHYSIQUE.md` §6.

### SS6 — Falsification de prix / survente sur la boutique (SR1)

Un attaquant modifie le prix envoyé au panier, ou lance N achats simultanés sur
le dernier article pour payer moins cher ou vendre plus que le stock.

- **Événement redouté** : ER9 — un article est facturé sous son prix catalogue,
  ou le stock devient négatif.
- **Chemin d'attaque** : `POST /checkout` avec un `price_cents` forgé ; rafale de
  `POST /checkout` sur le même panier ou sur le dernier article.
- **Mesures** : le prix n'est **jamais** lu dans la requête — le total est
  recalculé depuis `products.price_cents` en base
  (`test_un_prix_envoye_par_le_client_est_ignore`) ; le panier est réclamé par un
  `UPDATE ... WHERE checked_out_at IS NULL` atomique (un double-clic donne 409) ;
  le stock est décrémenté dans la même transaction avec `WHERE stock >= ?`
  (`test_deux_checkouts_sur_le_meme_panier`,
  `test_achats_simultanes_ne_survendent_pas`).
- **Résiduel** : une référence de commande fait 32 bits (`CMD-` + 8 hex) ; elle
  est donc énumérable. Elle ne donne aucun accès sans le capability token (SS7).
- **Preuve** : `tools/attack_shop.py` (familles « falsification de prix » et
  « course sur le stock »), détail dans [`docs/SECURITE.md`](SECURITE.md) §4-5.

### SS7 — Fuite de l'historique d'achats (énumération de références) (SR1)

Un attaquant essaie de lire les commandes d'un autre client en devinant une
référence `CMD-XXXXXXXX` ou un `customer_ref`.

- **Événement redouté** : ER10 — l'historique d'achats d'un client est lu par un
  tiers.
- **Chemin d'attaque** : `GET /orders/{reference}?customer_ref=...`, ou
  `GET /orders?customer_ref=...` avec un token inventé.
- **Mesures** : `customer_ref` est un **capability token** de 24 octets
  (`secrets.token_urlsafe(24)`, 192 bits), pas un identifiant devinable ; son
  format est validé **avant** toute comparaison, puis comparé en octets à temps
  constant. Le **détail** renvoie **404** (jamais 403) pour ne pas révéler
  l'existence de la commande (`test_lhistorique_exige_le_customer_ref`,
  `test_un_token_non_ascii_ne_fuit_pas_lexistence`) ; la **liste** exige un
  format valide (400 sinon) et renvoie 200 avec **zéro commande** si le token ne
  correspond à personne. `/admin/orders` exige une session back-office de rôle
  admin, validée auprès du back-office (`require_admin`).
- **Résiduel** : le capability token transite en paramètre de requête (donc
  visible dans les journaux d'accès et l'historique du navigateur) ; il n'y a pas
  de limite de débit sur `/orders`. Acceptable en démonstration locale, à
  corriger en production (en-tête ou cookie, quota par IP).
- **Preuve** : `tools/attack_shop.py` (famille « énumération d'historique »),
  [`docs/SECURITE.md`](SECURITE.md) §1 (F10) et §5.

---

## 5. Atelier 4 — Scénarios opérationnels et mesures

### 5.1 Chaîne de détection

| Mesure | Implémentation | Preuve |
|---|---|---|
| Features calculées par un module unique | `services/ml-detector/features.py` | 12 tests |
| Seuil **appris**, jamais codé en dur | `train.py` → `threshold.json` | `threshold_estimators` |
| Calibration hors échantillon (25 %) | `train.py` | `calibration_set: holdout_25pct` |
| Anti-rebond 3 fenêtres consécutives | `detector.py` | test dédié |
| Refus de conclure sous 8 événements | `MIN_EVENTS = 8` | test dédié |
| Sonde de liveness exclue | champ `synthetic` | test dédié |
| Purge de fenêtre avant toute sortie | `_purge_window` | test dédié |
| Seuil runtime refusé s'il vient d'un autre modèle | `model_trained_at` | `runtime_threshold_ignored` |

### 5.2 Chaîne cryptographique

| Mesure | Implémentation | Preuve |
|---|---|---|
| Signature d'anneau liée (bLSAG) | `monero-crypto-core/src/main.rs` | 9 tests Rust + 10 tests HTTP |
| Key image anti-double-dépense | `I = x·H_p(P)`, hash-vers-groupe sans log discret connu | test de forge rejetée |
| Confidentialité des montants | chiffrement par masque dérivé de l'ECDH | `audit-view` |
| Audit **view-only** (sans Spend Key) | `GET /audit/last`, `/crypto/audit-view` | test dédié |
| Engagements de Pedersen équilibrés | `/crypto/commit-split`, `/crypto/commitment-balance` | test de désaccord détecté |
| Entrées invalides → 400, jamais de panic | `BadRequest` | test Rust |

### 5.3 Remédiation et déception

| Mesure | Implémentation | Preuve |
|---|---|---|
| Remédiation asynchrone (pas de timeout) | `/incident` répond 202 | 5 tests d'intégration |
| Un seul incident à la fois | `asyncio.Lock` | test de concurrence |
| Quarantaine **ciblée** (outputs les plus anciens) | `/admin/quarantine-outputs` | 1 test |
| Durcissement réel de l'anneau | `/admin/ring-size` | 1 test |
| Déception active branchée | `waf` → `shadow-api` | 10 tests |
| Le client légitime n'est **jamais** dérouté | règle sur l'âge des leurres | test dédié |
| Playbook sans tâche décorative | `remediation_playbook.yml` | `ansible_rc`, sortie Ansible |
| Aucun accès Docker depuis le moteur | socket retiré | `docker-compose.yml` |

### 5.4 Gestion des secrets

| Mesure | Implémentation | Limite |
|---|---|---|
| Clé JWT dans Vault (KV v2) | `auth-service/main.py` | AppRole non provisionné |
| Fallback explicite, jamais silencieux | `/healthz` → `jwt_key_source` | — |
| Aucun secret dans le dépôt | `.gitignore`, `.env.example` | — |
| Rotation de clé par le playbook | étape 3, conditionnelle | dépend d'un token de service |
| **Authentification à deux facteurs du back-office** | identifiant + visage (SFace) + mot de passe, `services/backoffice` | pas de détection de vivacité |
| **Anti-bruteforce borné** | 2 échecs → 5 min, compteur en base, `Retry-After` | un déni de service ciblé reste possible (ER8) |
| **Journal d'audit des tentatives** | table `audit`, écran « Habilitations » | pas d'export signé (hors périmètre) |
| **Moindre privilège** | 3 rôles, dernier admin protégé, sessions révocables | pas de revue d'accès périodique |
| **Jetons générés, jamais choisis** | `tools/init_env.py` : `SERVICE_TOKEN` (32 octets), `BO_BOOTSTRAP_TOKEN`, `LED_DEVICE_TOKEN`, `TERMINAL_PIN`, mots de passe PostgreSQL/Grafana | un `.env` recopié sur un autre poste reste un secret partagé |
| **Fail-closed sans jeton** | sans `SERVICE_TOKEN`, `/admin/*`, `/incident`, `/state/report`, `/ingest` et `/alert` répondent **503** ; `/healthz` publie `service_token_configured: false` | une démo sans `.env` est bruyante, pas ouverte |
| **PIN du terminal plus jamais `1234`** | PIN à 4 chiffres tiré par `init_env.py` (9 000 valeurs), 5 refus → 60 s de blocage, indice retiré de l'interface | 4 chiffres restent une surface faible — c'est la surface volontaire qui alimente le ML |

Le jeton d'enrôlement `BO_BOOTSTRAP_TOKEN` est à **usage unique** : une fois le
premier administrateur créé, il se retire de `.env` et le service redémarre
(`/healthz` → `bootstrap_required: false`). Le laisser en place rouvrirait la
création de comptes à quiconque lit le fichier.

---

## 6. Atelier 5 — Risques résiduels

| # | Risque résiduel | Probabilité | Gravité | Traitement |
|---|---|---|---|---|
| RR1 | **Pas de preuve de domaine (Bulletproofs)** : un montant négatif modulo l passerait la vérification d'équilibre | Faible (attaquant authentifié) | 3 | **Accepté et documenté** (`/healthz` → `not_implemented`) |
| RR2 | bLSAG au lieu de CLSAG : signature ~2× plus grosse | Certaine | 1 | Accepté (aucun impact fonctionnel) |
| RR3 | Registre des outputs en mémoire : perte au redémarrage | **Fermé** | 2 | Journal PostgreSQL (`registry.py`, 16 tests) |
| RR4 | Modèle ML calibré sur un seul poste | Moyenne | 4 | **Réduit** : `capture_baseline.py` rejouable en 7 min, seuil runtime refusé s'il est périmé |
| RR5 | AppRole Vault non provisionné | Certaine | 2 | Accepté en démo, écrit dans le playbook |
| RR6 | Règles WAF limitées à l'âge des leurres | Moyenne | 2 | Accepté : le ML reste le détecteur, le WAF ne fait que filtrer au bord |
| RR7 | Images Docker non épinglées par digest | Moyenne | 3 | Recommandé (§9) |
| RR8 | **Pas de détection de vivacité faciale** : une photo imprimée passerait le facteur visage | Moyenne | 2 | **Accepté et documenté** (`/healthz` → `liveness: not_implemented`) ; le mot de passe reste exigé |
| RR9 | **Blocage de 5 min exploitable en déni de service** : un tiers peut bloquer un compte connu | Faible | 2 | Accepté (imposé par la consigne) ; déblocage par un administrateur, alerte `ComptesBloques` |
| RR10 | Back-office en HTTP sur `localhost` | Faible (trafic local) | 2 | Accepté en démo ; TLS recommandé en production |

---

## 7. PCA / PRA

### 7.1 Objectifs de continuité

| Service | RTO visé | RPO | Justification |
|---|---|---|---|
| `payment-api` | **15 s** | 0 (registre PostgreSQL) | C'est le service que la démo doit montrer disponible |
| `ml-detector` | **30 s** | n/a (modèle figé dans l'image) | Un redémarrage recharge les artefacts |
| `remediation-engine` | **10 s** | perte de `/history` | L'état est republié au démarrage (`IDLE`) |
| `monero-crypto-core` | **10 s** | n/a (stateless) | Aucun état à restaurer |
| `waf` | **5 s** | perte du compteur de déception | La déception est réarmée par le playbook |
| `front-dashboard` | **5 s** | n/a | Recalcule tout depuis SSE + `/score` |
| `vault` | **60 s** | données persistées (`vault-data`) | Hors chemin critique de la démo |
| `backoffice` | **15 s** | comptes, visages et blocages persistés (`bo-data`, SQLite) | Un redémarrage ne perd ni les comptes ni les blocages |
| `shop-api` | **15 s** | 0 (commandes, paiements et stock dans PostgreSQL) | Sans état : le balayage des commandes `pending` répare celles qu'un arrêt brutal a laissées en cours |
| `db-postgres` | **60 s** | **non garanti** : aucune sauvegarde automatique | Porte les commandes, les paiements, le stock **et** le registre d'outputs — une perte efface les deux |
| `rag-assistant` | **10 s** | n/a (index reconstruit au build) | Aucun état runtime ; sans Ollama, il répond en extractif |
| `loki` | **30 s** | perte des journaux non encore ingérés (rétention 30 j dans `loki-data`) | La supervision reste possible via Prometheus ; les journaux ne sont pas sauvegardés |
| Bandeau LED ESP8266 | **0** (non critique) | n/a | S'éteint s'il ne joint plus le back-office |
| Terminal physique | **0** (non critique) | n/a | Miroir d'état, jamais nécessaire |

**RTO agrégé de la chaîne de paiement : 15 s** — contre **6,6 / 7,1 / 7,2 s**
mesurées en reprise automatique sur trois exécutions consécutives
(`docs/RAPPORT.md` §6.2). La marge est volontaire : le RTO couvre le cas où la
remédiation échoue et où l'exploitant redémarre à la main.

**La base est le point de rupture de la boutique.** `payment-api` continue de
signer si PostgreSQL tombe (le registre dégrade en mémoire et compte ses pertes,
`lost_writes`), mais `shop-api` ne peut plus lire le catalogue ni écrire une
commande : la boutique est indisponible. Et `db-postgres` porte **deux**
historiques dans la même base `shadow_settle` : les commandes/paiements de la
boutique et le registre d'outputs du paiement. Sans `pg_dump` scripté, le RPO
n'est pas borné : c'est le premier chantier de sauvegarde (§9).

### 7.2 Scénarios de continuité

| Panne | Détection | Continuité | Reprise |
|---|---|---|---|
| `payment-api` tombe | `/healthz` + sonde `auto-probe` (`ok: false`) | Le WAF renvoie 502 explicite | `docker compose up -d payment-api` (coupure de quelques secondes, registre conservé) |
| `db-postgres` tombe | `shop_database_up 0`, `/healthz` dégradé | Le paiement continue en mémoire (`lost_writes`) ; la boutique répond 503 explicite | Redémarrage (le volume `pg-data` est conservé) ; en cas de perte du volume, **les commandes non sauvegardées sont perdues** (pas de PITR) |
| `shop-api` tombe | `payment-api` reste sain (`tools/chaos.py shop`) | Le terminal et le paiement direct continuent | Redémarrage ; le balayage expire les commandes `pending` |
| `rag-assistant` tombe | quadrant « Assistant » muet | Aucun impact sur le paiement ni la boutique | Redémarrage |
| `loki` tombe | Prometheus et Alertmanager continuent | Les alertes de logs ne partent plus | Redémarrage ; les journaux de la coupure sont perdus |
| `ml-detector` tombe | `e2e_demo.py` s'arrête au contrôle 1 | **Le paiement continue** (le ML n'est pas dans le chemin critique) | Redémarrage, artefacts rechargés |
| `remediation-engine` tombe | `/state` injoignable | Détection toujours visible sur `/score` | Redémarrage, `IDLE` republié |
| `waf` tombe | `/healthz` | Trafic direct sur `payment-api` possible | Redémarrage, `/admin/reset` |
| Ansible absent | `ansible_rc = 127` | — | `docker compose build remediation-engine` |
| Terminal débranché | Log du pont | Chaîne intacte | Rebrancher, resynchronisation `/state` |
| Seuil périmé | Score au repos > seuil | — | `capture_baseline.py` + `train.py` (7 min) |
| Registre épuisé | 409 en série | — | Ré-alimentation automatique + redémarrage |

### 7.3 Sauvegarde

| Donnée | Stratégie | Test de restauration |
|---|---|---|
| Code et configuration | Git (dépôt distant) | `git clone` sur un poste neuf |
| Artefacts ML | Reconstruits par `train.py` (déterministe, graine fixe) | `train.py --check` |
| Secrets | Vault (`vault-data`) ; jamais dans Git | `vault kv get secret/jwt` |
| Registre des outputs | PostgreSQL (`outputs`, `key_images`, `payments`) | **à faire** : `pg_dump` + restauration ; le volume `pg-data` ne protège pas d'une corruption |
| Commandes, paiements et stock | PostgreSQL (`orders`, `order_items`, `payments`, `products`) | **à faire** : même `pg_dump` — aucune sauvegarde automatique aujourd'hui |
| Journaux Loki | non sauvegardés (rétention 30 j dans `loki-data`) | — |
| Index RAG | reconstruit par `build_index.py` au build de l'image | `docker compose build rag-assistant` |
| Modèle 3D | `hardware/models/*.scad` (paramétrique) | Réimpression |

**Un volume Docker n'est pas une sauvegarde.** `pg-data` et `loki-data`
survivent à un `docker compose down`, pas à la perte du disque, à une corruption
ou à un `DROP TABLE`. Tant que `pg_dump` n'est pas scripté, le RPO de
`db-postgres` reste **non borné** — c'est écrit ici plutôt que supposé.

---

## 8. NIS2 et DORA — correspondance

### 8.1 NIS2 (directive UE 2022/2555), article 21

| Exigence | Mesure dans ce système | Preuve |
|---|---|---|
| 21.2.a — analyse de risques | Ce document, EBIOS RM | §2-6 |
| 21.2.b — gestion des incidents | Détection ML + remédiation autonome | `e2e_demo.py` |
| 21.2.c — continuité d'activité | PCA/PRA, RTO par service | §7 |
| 21.2.d — sécurité de la chaîne d'approvisionnement | Versions figées (`requirements.txt`, `Cargo.lock`) | `--locked` |
| 21.2.e — sécurité du développement | Tests automatisés (**221** : 197 pytest, 5 script, 9 Rust, 10 HTTP), audit de code | `tools/run_tests.sh` |
| 21.2.f — évaluation de l'efficacité | `validate_real.py`, `analyze_separation.py` | §5.1 |
| 21.2.g — hygiène et formation | Hors périmètre technique | — |
| 21.2.h — cryptographie | Ristretto255, bLSAG, Pedersen, View Key séparée | §5.2 |
| 21.2.i — ressources humaines, contrôle d'accès | Trois rôles back-office, JWT court (300 s), PIN terminal | `backoffice`, `auth-service` |
| 21.2.j — MFA | **Back-office : COUVERT** (identifiant + visage + mot de passe). **Terminal de paiement : NON COUVERT** (PIN seul) | `services/backoffice`, `payment-api` |

**Pourquoi la ligne MFA est coupée en deux.** Le back-office exige trois
éléments : un identifiant, un **visage** (descripteur biométrique) puis un
**mot de passe**. Ce sont deux facteurs distincts — quelque chose que l'on est,
quelque chose que l'on sait — et les tests le prouvent (`test_visage_reconnu_puis_mot_de_passe_ouvre_la_session`,
`test_le_mot_de_passe_nest_pas_accepte_sans_visage`). Le **terminal de
paiement**, lui, ne demande qu'un PIN : c'est volontaire, c'est la surface
d'attaque qui donne au détecteur ML quelque chose à voir. Écrire « MFA : non
couvert » sans distinguer les deux serait faux dans un sens et dangereux dans
l'autre.

### 8.2 DORA (règlement UE 2022/2554)

| Exigence | Mesure | Preuve |
|---|---|---|
| Art. 5 — gouvernance du risque TIC | Ce dossier, propriétaire identifié par brique | §2.2 |
| Art. 6 — cadre de gestion du risque | EBIOS RM + registre des risques résiduels | §6 |
| Art. 9 — protection et prévention | Chiffrement des montants, anti-rejeu, durcissement | §5.2 |
| Art. 10 — détection | ML non supervisé, seuil appris, anti-rebond | §5.1 |
| Art. 11 — réponse et rétablissement | Remédiation autonome + déception + preuve de reprise | §5.3 |
| Art. 12 — sauvegarde et restauration | §7.3 — **sauvegarde PostgreSQL encore à scripter** | — |
| Art. 13 — apprentissage | **12 failles** (F1–F12) et **4 bugs critiques** documentés et corrigés | [`docs/SECURITE.md`](SECURITE.md) |
| Art. 17 — gestion des incidents | `/incident`, `/alert`, `/history`, `diagnostic` | `remediation-engine` |
| Art. 19 — **traçabilité** | Audit **view-only** (montants lisibles sans capacité de dépense), journal d'audit du back-office, journaux Loki | `/audit/last`, `/admin/audit`, `loki` |
| Art. 28 — risque tiers | **NON COUVERT** (pas de fournisseur critique) | — |

### 8.3 Ce que la démonstration prouve, et ce qu'elle ne prouve pas

**Prouvé** : détection non supervisée, remédiation sans intervention humaine,
preuve de reprise par une transaction réelle, containment de l'attaquant,
auditabilité sans capacité de dépense.

**Non prouvé** : résistance à un attaquant qui possède les clés, conformité
formelle (pas d'audit externe), MFA **du terminal** (celui du back-office est
prouvé à deux facteurs), gestion des tiers, plan de formation, conformité RGPD
formelle (pas d'AIPD, pas de registre de traitement — voir §8.4).

### 8.4 Données personnelles et biométrie (RGPD)

**Le système n'est pas « sans données personnelles ».** Le back-office stocke un
descripteur facial, des identifiants, des adresses IP et un journal d'audit. Un
descripteur facial est une **donnée biométrique** au sens de l'article 4.14 du
RGPD (données personnelles résultant d'un traitement technique spécifique
relatif aux caractéristiques physiques, physiologiques ou comportementales d'une
personne, qui permettent ou confirment son identification unique), et son
traitement est **interdit par défaut** par l'article 9.2, sauf exception.
L'écrire est plus solide que l'omettre : un jury NIS2/DORA le verra dans le code.

**Ce n'est pas un traitement réel.** Les personnes enrôlées sont les membres du
groupe, avec leur accord ; aucun client réel, aucun salarié, aucune production.
Les obligations ci-dessous sont donc décrites *comme si* le SI était déployé,
mais ne sont pas toutes remplies — c'est écrit en fin de section.

#### Nature des données

| Donnée | Où | Qualification | Pourquoi elle existe |
|---|---|---|---|
| Descripteur facial (128 flottants, BLOB) | `users.face_embedding` (SQLite `bo-data`) | **donnée biométrique, art. 9** | vérifier « je suis bien cette personne » |
| Identifiant, rôle, état actif | `users` | donnée personnelle ordinaire | compte et contrôle d'accès |
| Adresse IP, user-agent | `sessions` (IP, user-agent), `audit` (IP) | donnée personnelle (identifiant indirect) | sécurité et traçabilité |
| Journal d'audit (événement, horodatage, cible, IP) | `audit` | donnée personnelle | preuve des accès et des changements |
| Compteur d'échecs et blocage | `attempts` | donnée personnelle | anti-bruteforce |
| **Image JPEG de la caméra** | **nulle part** | — | décodée en mémoire, descripteur calculé, image jetée |

**Aucune image n'est conservée.** `POST /auth/identify` décode le base64, calcule
le descripteur SFace, puis jette l'image : le seul artefact stocké est
`face_embedding` (512 octets). L'image décodée est bornée à 6 Mo et
redimensionnée (1280 px maximum). Un visage au premier plan suffit ; un second
visage comparable au premier est refusé (`multiple_faces`). Aucun journal ne
contient l'image ni le descripteur.

#### Base légale

| Traitement | Base retenue | Pourquoi |
|---|---|---|
| Reconnaissance faciale du back-office | **consentement explicite, art. 9.2.a** | personnes enrôlées volontairement, démonstration ; le consentement doit être recueilli avant l'enrôlement et pouvoir être retiré |
| Journal d'audit, IP, anti-bruteforce | **intérêt légitime, art. 6.1.f** | sécurité du SI ; aucune biométrie dans ces tables |

**Limite honnête** : pour des **salariés**, le consentement n'est pas libre
(lien de subordination) ; un déploiement réel devrait chercher une autre base
(art. 9.2.b obligation légale, ou 9.2.g intérêt public important) et la faire
valider juridiquement. Pour un SI fictif d'école, le consentement explicite des
étudiants est la base la plus défendable — à condition de le documenter.

#### Minimisation et durées

| Donnée | Durée | Mécanisme |
|---|---|---|
| Descripteur facial | tant que le compte existe ; supprimé au retrait du visage | `DELETE /admin/users/{id}/face` → `face_embedding = NULL` |
| Sessions | 8 h maximum, 30 min d'inactivité | purge à chaque étape d'authentification (`purge_expired`) |
| Challenges | 90 s | purge |
| Journal d'audit | **pas de purge aujourd'hui** | à fixer (proposition : 12 mois) |
| Compteur d'échecs | jusqu'au déblocage ou à la réinitialisation | pas de purge automatique |

**Minimisation** : pas d'image, pas de gabarit 3D, pas de vidéo, pas de
géolocalisation, pas de donnée de paiement nominative dans le back-office. Le
descripteur n'est pas une image, mais **ce n'est pas une donnée anonyme** : il
permet de reconnaître, et des travaux montrent qu'une reconstruction partielle
du visage est possible. Il est donc traité comme une donnée sensible, pas comme
un simple hash.

#### Droits des personnes

| Droit | Ce qui existe | Ce qui manque |
|---|---|---|
| Accès | `GET /admin/users`, `GET /admin/audit` (rôle admin) | export lisible par la personne elle-même |
| Rectification | ré-enrôlement du visage, changement de mot de passe | — |
| Effacement | `DELETE /admin/users/{id}/face` (rôle admin) supprime le descripteur ; la désactivation coupe l'accès | **la désactivation ne supprime pas le descripteur** ; pas de suppression physique du compte |
| Opposition / limitation | désactivation du compte | pas de procédure dédiée ni de trace de la demande |
| Portabilité | — | pas d'export structuré |

#### Sécurité du traitement

| Mesure | Où |
|---|---|
| Mot de passe : PBKDF2-HMAC-SHA256, 600 000 itérations, sel 16 octets | `services/backoffice/security.py` |
| Sessions opaques : 32 octets aléatoires, seul le SHA-256 est stocké | `services/backoffice/db.py` |
| 2 échecs → 5 min de blocage, compteur en base | `services/backoffice/main.py` |
| Journal d'audit de chaque étape (visage, mot de passe, blocage, admin) | table `audit` |
| Anti-énumération : identifiant inconnu, compte inactif et visage absent → même réponse | `identify`, `_verify_merchant` |
| Trois rôles, dernier admin protégé, sessions révocables | `require_role`, `refuse_if_last_admin` |
| Aucune image conservée ; image décodée bornée ; un visage au premier plan | `faces.py`, `config.py` |

**Faiblesses assumées** : pas de TLS (O1), donc le descripteur circule en clair
sur le réseau local ; le descripteur n'est **pas chiffré au repos** (fichier
SQLite dans le volume `bo-data`) ; pas de détection de vivacité (O2), une photo
imprimée passe le facteur visage — le mot de passe reste exigé.

#### Ce qui reste à faire pour être conforme

1. **Registre de traitement** (art. 30) : finalité, base légale, catégories,
   destinataires, durées, mesures de sécurité. Il n'existe pas.
2. **Information des personnes** (art. 13/14) avant l'enrôlement : qui traite,
   pourquoi, combien de temps, comment retirer son consentement.
3. **DPO** (art. 37) : à désigner ou à documenter comme non requis ; aucun
   contact n'est publié.
4. **AIPD** (art. 35) : la biométrie est un critère de risque élevé. Même pour
   une démonstration, l'analyse doit être écrite — ne serait-ce que pour conclure
   qu'elle est proportionnée.
5. **Recueil et registre du consentement** explicite, avec date et possibilité
   de retrait ; aujourd'hui l'enrôlement suppose l'accord mais ne le trace pas.
6. **Politique de rétention** : purger l'audit (12 mois ?), les tentatives
   (90 jours ?), et supprimer le descripteur à la désactivation.
7. **Chiffrement au repos** du fichier SQLite (disque chiffré ou SQLCipher) et
   TLS en production.
8. **Procédure d'effacement complet**, y compris dans les sauvegardes, et export
   des données de la personne.

**Conclusion** : en l'état, ce SI de démonstration **ne serait pas conforme**
pour un traitement réel de données biométriques — non par malice, mais parce que
le registre, l'information, l'AIPD et le chiffrement au repos manquent. Ces
manques sont écrits ici, ce qui vaut mieux qu'une phrase « aucune donnée
personnelle » que le premier `grep` contredirait.

---

## 9. Recommandations à la direction

| Priorité | Recommandation | Coût | Effet |
|---|---|---|---|
| 1 | **Bulletproofs** (preuves de domaine) | 3-5 j | Ferme RR1, seule vraie faille cryptographique |
| 2 | **AppRole Vault** + politique dédiée | 0,5 j | Supprime le token de service du playbook (RR5) |
| 3 | ~~**Registre persistant**~~ — **fait** | 2 j | Ferme RR3 (`registry.py`, 16 tests) |
| 4 | **Sauvegarde PostgreSQL** (`pg_dump` + test de restauration) | 0,5 j | Borne le RPO de `db-postgres` : commandes **et** registre d'outputs |
| 5 | Images Docker épinglées par digest + scan Trivy | 0,5 j | Ferme RR7, exigence NIS2 21.2.d |
| 6 | Recalibrage du ML sur le PC de démonstration | 0,25 j | Ferme RR4 |
| 7 | MFA sur le terminal (PIN + challenge) | 1 j | NIS2 21.2.j (le back-office est déjà à deux facteurs) |
| 8 | **Registre RGPD + AIPD + information des personnes** | 1 j | §8.4 : aujourd'hui le traitement biométrique n'est pas documenté |
| 9 | Observabilité : tableaux de bord et alertes sur `/healthz` | 1 j | Détection des pannes silencieuses (SR3) |

**Message de synthèse** : le système est *techniquement* résilient (reprise en
**6,6 à 7,2 s**, 0 faux déclenchement, containment de l'attaquant) et
*honnêtement* documenté. Le risque résiduel dominant n'est pas l'attaque : c'est
la panne silencieuse, et derrière elle la **perte de la base** (commandes +
registre). Les priorités 4, 6 et 8 sont donc les moins coûteuses et les plus
rentables pour la soutenance ; la 1 reste la seule dette cryptographique.

---

## 10. Ce qui n'est pas couvert

- **EBIOS RM atelier 5 formel** : les mesures sont listées et implémentées,
  mais aucun plan d'action chiffré ni indicateur de suivi.
- **Homologation** : pas d'audit externe, pas de pen-test formel.
- **RGPD** : le traitement biométrique **existe** (§8.4) — descripteur facial,
  identifiants, IP, journal d'audit. Manquent le **registre de traitement**,
  l'**information des personnes**, la désignation d'un **DPO**, l'**AIPD** et le
  **chiffrement au repos**. En l'état, un déploiement réel serait non conforme.
- **Sauvegarde / restauration** : aucun `pg_dump` scripté ; le RPO de
  `db-postgres` n'est pas borné et aucune restauration n'a été testée.
- **Mesures Docker sur ce poste** : non réalisées (Docker absent). Les chiffres
  de reprise (6,6 / 7,1 / 7,2 s) viennent de la pile de démonstration, pas d'une
  mesure faite ici.
- **Plan de formation et sensibilisation** : hors périmètre étudiant.
- **PCA organisationnel** (astreinte, cellule de crise) : non traité.
