# Dossier d'ingénierie — RESILIENCE-ALPHA / SHADOW-SETTLE

**Workshop M2 — 2026**
Système de paiement fictif à confidentialité cryptographique, avec détection
d'anomalies par apprentissage automatique et remédiation autonome.

> Ce dossier décrit le système **tel qu'il tourne**, pas tel qu'il pourrait
> tourner. Chaque affirmation renvoie à un fichier, un endpoint ou une mesure
> reproductible. Les limites sont au §7, et elles sont assumées.

---

## 1. Le problème traité

Le sujet impose quatre contraintes qui, ensemble, interdisent les solutions
habituelles :

1. **Une attaque réelle**, lancée depuis un poste distinct.
2. **Une détection par machine learning**, explicitement « et non par de simples
   seuils fixes » — un seuil codé en dur est éliminatoire dans l'esprit de la
   grille.
3. **Une remédiation automatique** : toute intervention humaine pendant la
   démonstration donne 0/5 sur l'axe principal.
4. **Une preuve** : une nouvelle transaction doit réussir après l'incident.

La difficulté n'est pas de faire chacune de ces choses. C'est de les enchaîner
**en moins de 3 minutes, sans intervention, de façon répétable**. Une démo qui
marche une fois sur deux vaut zéro point.

Notre réponse tient en une phrase : *rendre l'attaque physiquement visible dans
les données, apprendre le seuil qui la sépare du trafic normal, et faire porter
la preuve de reprise par une transaction réelle plutôt que par un log.*

---

## 2. Architecture

### 2.1 Vue d'ensemble

```
   PC Attaquant                     PC Hôte (docker compose, 19 services)
   ┌──────────┐
   │ attack.py│──► waf:8085 ──┬──► payment-api:8080 ──► monero-crypto-core:8081
   └──────────┘   (proxy)     │         │                        (Rust)
                              │         │ télémétrie
                              │         ▼
                              │    ml-detector:8083
                              │         │ POST /incident
                              │         ▼
                              │    remediation-engine:8084
                              │         │ ansible-playbook
                              │         ├──────────► waf:8085/admin/redirect
                              │         ├──────────► payment-api:8080/admin/*
                              │         ├──────────► vault:8200 (rotation clé)
                              │         └──────────► SSE /events
                              │                        │
                              │              ┌─────────┴─────────┐
                              └──► shadow-api │ front-dashboard   │ hardware_bridge
                                   (honeypot) │ (anneau + score)  │ (ESP32, hôte)
                                              └─────────┬─────────┘
                                                        │ session (Bearer)
                                              ┌─────────▼─────────┐
   caméra USB ──── image JPEG ───────────────►│ backoffice:8086   │
   (PC hôte, navigateur)                      │ YuNet + SFace     │
                                              │ SQLite + audit    │
                                              └─────────┬─────────┘
                                                        │ GET /led/state (WiFi)
                                              ┌─────────▼─────────┐
                                              │ ESP8266 + LED RGB │
                                              └───────────────────┘
```

### 2.2 Les cinq piliers, et ce qui les relie réellement

| Pilier | Composants | Lien avec les autres |
|---|---|---|
| **DEV** | `payment-api`, `monero-crypto-core`, `front-dashboard` | le dashboard consomme SSE et `/score` ; l'API appelle le noyau Rust à chaque transaction |
| **INFRA** | `docker compose`, `ansible/`, `prometheus`, `alertmanager`, `loki`, `grafana` | le moteur exécute un vrai playbook Ansible ; Prometheus scrape 12 services et pousse ses alertes au moteur |
| **IA/DATA** | `ml-detector` (scikit-learn + PyTorch) | publie l'incident au moteur ; expose le score au dashboard |
| **CYBER** | `waf`, `shadow-api`, `auth-service`, `vault`, **`backoffice`** | le WAF déroute vers le honeypot ; Vault détient la clé JWT ; Ansible fait tourner cette clé ; le back-office contrôle l'accès au tableau de supervision |
| **TRANSFORMATION** | `docs/GOUVERNANCE.md` | EBIOS RM et PCA/PRA reliés aux composants ci-dessus, pas à un schéma théorique |

Un pilier n'est pas « présent » parce qu'un conteneur porte son nom. Il l'est
quand un flux le traverse. C'est ce que vérifie `tools/e2e_demo.py`.

### 2.3 Décisions structurantes

**Une seule source de vérité pour l'état.** `remediation-engine` publie
(`StateBus`), le dashboard et le terminal consomment. Aucun des deux ne
recalcule l'état. Deux logiques parallèles finiraient par diverger le jour de la
démonstration.

**Le terminal physique ne peut pas casser la démo.** Aucun abonné SSE n'est
obligatoire ; le pont est strictement en lecture ; aucun chemin ne va du
terminal vers le moteur. Un test vérifie l'absence de POST dans le pont.

**La détection est hors du chemin critique.** Si `ml-detector` tombe, le
paiement continue. C'est volontaire : le ML observe, il ne filtre pas.

**Le WAF est le seul point d'entrée « client ».** L'attaquant y passe, le client
légitime aussi — mais seule la transaction qui porte le signal d'attaque est
déroutée. Une déception qui casse le paiement du client serait pire que pas de
déception.

### 2.4 L'authentification du back-office (consigne additionnelle)

Le tableau de supervision n'est plus accessible sans franchir trois étapes :
identifiant, **reconnaissance faciale sur la caméra USB du PC hôte**, mot de
passe. Un bandeau LED ESP8266 rend chaque étape visible à distance.

```
 navigateur (PC hôte)                 docker (PC hôte)
 ┌────────────────────┐   JPEG    ┌──────────────────────────────┐
 │ getUserMedia       │──────────►│ backoffice:8086              │
 │ caméra USB         │           │  YuNet → SFace → cosinus     │
 └────────────────────┘           │  SQLite : comptes, sessions,  │
          ▲                       │  blocages, audit             │
          │ état                  └──────────────┬───────────────┘
 ┌────────┴──────────┐   GET /led/state (300 ms) │
 │ ESP8266 + LED RGB │◄──────────────────────────┘
 └───────────────────┘
```

Trois décisions qui méritent une phrase en Q&A :

**La décision de sécurité est côté serveur.** Le navigateur n'annonce jamais
« visage reconnu » : il envoie une image, le serveur calcule le descripteur et
le compare. Un client modifié ne peut pas sauter l'étape 2. Le compteur
d'échecs et le blocage de 5 minutes vivent en base : recharger la page ne les
remet pas à zéro.

**L'ESP interroge, il n'est pas poussé.** Le PC hôte est le point d'accès : la
carte est derrière son NAT. Un POST serveur → carte exigerait une IP fixe, une
réservation DHCP et une règle de pare-feu entrante — trois causes de panne pour
300 ms de latence gagnées. Le firmware reste maître du temps (30 s de vert fixe,
10 flashs rouges) et **s'éteint s'il ne joint plus le back-office** : pas de feu
vert sans service.

**Le visage est un facteur d'identité, pas un anti-spoofing.** Une photo
imprimée passerait ; c'est publié dans `/healthz` (`liveness: not_implemented`).
Le mot de passe reste le second facteur, et il n'est demandé qu'après la
reconnaissance.

Coût mesuré : **29 ms** par visage sur un CPU portable, 38 Mo de modèles
téléchargés et vérifiés par SHA-256 **à la construction de l'image** — aucun
téléchargement au runtime, donc aucun échec réseau possible pendant la
démonstration.

### 2.5 La boutique : un site vitrine, pas un tableau de bord

Les règles du jury du 20/10 déplacent le centre de gravité : le SI doit être un
**site de paiement** avec des articles en base, un front, un back, des commandes
et un historique. `services/shop-api` (PostgreSQL, port 8087) le porte.

| Élément | Réalisation |
|---|---|
| Articles | table `products` (SKU, prix, stock, actif), catalogue de 6 articles amorcé une fois |
| Panier | tables `carts` / `cart_items`, quantité bornée (1..20) |
| Commande | `orders` + `order_items` (photo du prix au moment de l'achat) |
| Paiement | `payments` + `payment_events`, statut `pending → paid | failed` |
| Preuve | `payment-api /tx/pay` : output du **montant exact**, signature d'anneau bLSAG, key image |
| Historique | `GET /orders` par **capability token** (`customer_ref` 256 bits) |
| Back | `AdminShop.jsx` : prix, stock, retrait, commandes et preuve |

Trois décisions qui comptent :

**Le stock est réservé dans une transaction, puis restitué si le paiement
échoue.** Un `UPDATE products SET stock = stock - ? WHERE stock >= ?` dont le
nombre de lignes affectées est vérifié : deux acheteurs simultanés sur le
dernier article donnent une commande et un `409`, jamais un stock négatif. Un
échec de paiement restaure le stock et laisse le panier intact.

**Une commande n'est `paid` qu'après une preuve cryptographique.** Le montant
vient du serveur, jamais du client ; la key image renvoyée par le noyau est
stockée dans `payments`. Un paiement refusé produit une commande `failed` avec
son motif dans `payment_events` — visible par le client et par l'administrateur.

**L'administration n'utilise pas le jeton de service.** Le navigateur envoie la
session opaque du back-office ; `shop-api` la valide auprès du back-office et
exige le rôle `admin`. Le jeton de service reste serveur-à-serveur.

### 2.6 L'IA : RAG et ingestion SIEM

Deux briques répondent à « l'IA actualise ses compétences via le RAG » et
« l'IA gère les alertes des outils de supervision de sorte à agir elle-même ».

**RAG (`services/rag-assistant`, 8091).** Index BM25 construit **à la
construction de l'image** sur `docs/`, `README.md` et `TODO.md` : 13 documents, 372
passages. `POST /ask` renvoie une réponse **avec ses sources**. Si Ollama
(`llama3.1:8b`) est joignable, il rédige à partir des passages ; sinon le
service répond en **extractif** — les phrases du dossier, citées — et le dit
dans `/healthz` (`generation.ollama: unreachable`). Un assistant qui invente
serait pire qu'un assistant qui cite.

**Ingestion SIEM (`POST /alert`).** Prometheus évalue 24 règles, Alertmanager
les pousse au moteur de remédiation. Une liste **fermée** d'alertes est
actionnable (`NoyauCryptoIndisponible`, `SondeTransactionEnEchec`,
`WafIndisponible`…) ; les alertes de confort (bandeau LED, compte bloqué) sont
journalisées sans ouvrir d'incident. Le moteur réutilise **exactement** le
chemin de l'incident ML : même verrou, même playbook, même preuve de reprise.
Deux chemins de remédiation finiraient par diverger.

---

## 3. La détection : apprendre, pas seuiller

### 3.1 Le pipeline

```
payment-api ──observations brutes──► ml-detector
   (faits)                          ├── features.py  (6 features, 1 seule définition)
                                    ├── StandardScaler   (appris)
                                    ├── IsolationForest  (appris)
                                    ├── Autoencoder 6-4-2-4-6 (appris)
                                    └── seuil appris → threshold.json
```

`features.py` est **partagé** entre l'entraînement et l'inférence. C'était le
défaut majeur du squelette : entraînement sur des distributions inventées
(`cpu=4000 µs`, `entropie=3,2`), inférence sur des zéros. Le modèle ne pouvait
pas fonctionner, quel que soit le seuil.

Les 6 features :

| # | Feature | Ce qu'elle mesure |
|---|---|---|
| 0 | `decoy_median_age_days` | âge médian des leurres de l'anneau |
| 1 | `ring_verify_cpu_us` | coût de la vérification cryptographique |
| 2 | `key_image_rejection_ratio` | taux de rejets (double dépense, PIN) |
| 3 | `inter_arrival_median` | cadence médiane des requêtes |
| 4 | `ring_age_skewness` | asymétrie des âges de l'anneau |
| 5 | `tx_latency_p95` | latence perçue par le client |

### 3.2 Ce que la mesure a imposé de changer

Quatre erreurs de conception ont été trouvées **par la mesure**, pas à la
lecture du code. Chacune produisait des faux positifs massifs.

| Défaut | Symptôme observé | Correction |
|---|---|---|
| Entropie calculée sur les âges **poolés** de la fenêtre | 3,0 bits à 10 échantillons, 5,4 bits à 160, **à distribution identique** : la feature mesurait la cadence | Calcul **par événement** puis moyenne |
| `inter_arrival` en écart-type | Une pause de 6 s suffisait à produire un score de 6,2 | **Médiane** des intervalles |
| Sonde de liveness dans la télémétrie | Cadence fixe de 4 s, pool de leurres qui dérivait | Champ `synthetic`, exclu du calcul |
| Entropie comme feature principale | AUC 0,56 : elle sature à 10 leurres par transaction | Remplacée par l'**âge médian des leurres** (AUC 1,00) |

Avant ces corrections : 70 à 100 % de faux déclenchements sur du trafic
parfaitement normal. Après : voir §3.4.

### 3.3 Pourquoi l'âge médian des leurres

Monero tire ses leurres selon une **gamma biaisée vers les outputs récents** :
un wallet ne pioche pas uniformément dans le registre. Un attaquant qui veut
empoisonner l'anneau n'a pas accès à cette distribution — il lit le registre
public et tire uniformément.

Mesuré sur ce système (`analyze_separation.py`, 1 173 fenêtres par classe) :

| Feature | Normal | Attaque | AUC |
|---|---|---|---|
| `decoy_median_age_days` | **3,850 ± 0,167** | **25,731 ± 1,859** | **1,000** |
| `ring_age_skewness` | 0,934 ± 0,191 | 1,456 ± 0,226 | 0,947 |
| `ring_verify_cpu_us` | 2 502 ± 573 | idem | 0,500 |
| `inter_arrival_median` | 0,356 ± 0,356 | idem | 0,500 |
| `tx_latency_p95` | 46,10 ± 12,76 | idem | 0,500 |
| `key_image_rejection_ratio` | 0 | 0 | 0,500 |

**Lecture honnête** : une seule feature discrimine l'attaque principale. Les
autres mesurent des attaques *différentes* (rejeu de key image, rafale
automatisée, anneau malformé) et restent dans le vecteur pour cette raison — un
modèle à une seule feature ne détecterait qu'une attaque. C'est un choix
défendable, à condition de le dire.

### 3.4 Le seuil, et pourquoi il a changé deux fois

Le seuil est **appris**, jamais codé en dur. Il est le maximum de deux
estimateurs calculés sur un jeu de calibration **hors échantillon** (25 % de la
baseline, jamais vus à l'entraînement).

Sur 1 129 fenêtres réelles, nous avons comparé les estimateurs :

| Estimateur | Seuil | FPR | FPR sur 3 fenêtres consécutives |
|---|---|---|---|
| moyenne + 4σ | 2,97 | 1,42 % | **0,89 %** |
| 99,5ᵉ percentile | 3,44 | 0,53 % | 0,18 % |
| moyenne + 6σ | 4,24 | 0,27 % | 0,09 % |
| **99,9ᵉ percentile + 6σ** | **4,61** | **0,27 %** | **0,09 %** |

La règle initiale (`max(p99.5, m+4σ)`) donnait 0,89 % de déclenchements sur
trois fenêtres consécutives : un faux positif toutes les ~2 minutes de trafic
dense. Inacceptable pour une démo de 3 minutes. Le seuil retenu est le max du
99,9ᵉ percentile et de `moyenne + 6σ`.

Le coût des deux erreurs n'est pas symétrique : un faux déclenchement pendant la
démo coûte 25 points, une détection 2 s plus tardive coûte 2 secondes. Et la
marge est énorme (§3.5), donc on peut être conservateur sans rien perdre.

**Anti-rebond** : 3 fenêtres consécutives au-dessus du seuil sont exigées. Une
fenêtre isolée ne déclenche rien.

**Refus de conclure** : sous 8 événements dans la fenêtre, le détecteur reste en
`warming` et ne produit aucun score. Mieux vaut ne rien affirmer que produire un
faux positif sur du bruit d'échantillonnage.

### 3.5 Résultats mesurés

Validation par le **vrai chemin de production** (`validate_real.py` : trafic
légitime et attaque envoyés via `payment-api`, aucun montage de test) :

```
seuil                4,609
trafic normal        moyenne 0,80   max 1,24
attaque              moyenne 14,62  min 14,29
faux positifs        0 %            (0 déclenchement)
détection            100 %
```

Le score le plus haut du trafic normal est **3,7× sous** le seuil ; le score le
plus bas de l'attaque est **3,1× au-dessus**. Aucun chevauchement.

---

## 4. La cryptographie

### 4.1 Ce qui est réellement implémenté

`monero-crypto-core` (Rust, Ristretto255) expose 13 routes. Un modèle Monero
simplifié, mais dont **aucune primitive de sécurité n'est simulée** :

| Primitive | Formule | Rôle |
|---|---|---|
| Clés | `A = aG`, `B = bG` | View Key / Spend Key séparées |
| Adresse one-time | `P = H_s(rA)G + B`, `R = rG` | une adresse par transaction |
| Clé dérivée porteur | `x = H_s(aR) + b` | seul le porteur peut dépenser |
| **Key image** | `I = x·H_p(P)` | anti-double-dépense |
| **Signature d'anneau** | bLSAG sur `n` clés, message lié | prouve la possession sans révéler laquelle |
| Engagements | `C = aG + rH`, `H` sans log discret connu | montants cachés, homomorphes |
| Équilibre | `∑C_in − ∑C_out = 0` | conservation de la valeur |
| Montant | masque `Keccak("amount" ‖ shared)` | confidentialité |
| Audit | `GET /audit/last` avec la **seule** View Key | conformité DORA art. 19 |

### 4.2 Le détail qui fait la différence

La key image utilise un **hash-vers-le-groupe** (`SHA-512` →
`RistrettoPoint::from_uniform_bytes`, projection uniforme de type Elligator).

La tentation naturelle est de définir `H_p(P) = h(P)·G` avec `h` un hash vers un
scalaire. C'est plus simple à écrire, et **ça détruit l'anti-double-dépense** :
n'importe qui peut choisir une key image arbitraire `I = zG`, puis calculer
`R_π = (u·h + c_π·z)G` et produire une signature valide pour un output qu'il ne
possède pas. L'anti-double-dépense devient décoratif.

Avec un vrai hash-vers-le-groupe, personne ne connaît le logarithme discret de
`H_p(P)` — y compris nous. La key image ne peut être produite qu'en connaissant
`x`. C'est le genre d'erreur qui ne se voit pas à la lecture et qui détruit la
crédibilité technique du projet en Q&A.

### 4.3 L'audit view-only

La fonction de gouvernance : un auditeur qui détient **uniquement la View Key**
peut lire le montant d'une transaction, mais ne peut pas la signer, donc pas la
dépenser. C'est une séparation mathématique, pas une séparation de droits
logiciels.

```bash
curl -s localhost:8080/audit/last
# {"available": true, "amount": 323, "capability": "view-only (aucune capacité de signature)"}
```

### 4.4 Écarts assumés avec Monero de production

| Écart | Conséquence | Statut |
|---|---|---|
| **bLSAG** au lieu de **CLSAG** | signature ~2× plus grosse (linéaire en N) | même modèle de sécurité, optimisation de taille non faite |
| **Pas de Bulletproofs** | un montant négatif modulo l satisfait `∑C = 0` | **vraie faiblesse**, publiée dans `/healthz` |
| Pas de mnémonique | les clés sont des scalaires, pas une phrase | hors périmètre démo |
| Registre PostgreSQL | persistant ; repli mémoire explicite sans `DATABASE_URL` | fermé (§7) |

Ces trois lignes sont dans `GET /healthz` (`not_implemented`) et dans le dossier
de gouvernance. Un système qui prétend ne pas avoir de limites perd toute
crédibilité au premier contrôle.

---

## 5. La remédiation et la déception

### 5.1 Séquence complète, sans intervention

| Étape | Composant | Action réelle |
|---|---|---|
| 1 | `ml-detector` | 3 fenêtres anormales → `POST /incident` |
| 2 | `remediation-engine` | répond 202 immédiatement, publie `UNDER_ATTACK` |
| 3 | (attente 3 s) | le rouge est visible à l'écran et sur l'objet |
| 4 | `remediation-engine` | publie `REMEDIATING`, lance `ansible-playbook` |
| 5 | Ansible | **déception** : `POST waf:8085/admin/redirect` |
| 6 | Ansible | **quarantaine** : `POST payment-api/admin/quarantine-outputs` (25 plus anciens) |
| 7 | Ansible | **durcissement** : `POST payment-api/admin/ring-size` (11 → 25) |
| 8 | Ansible | **rotation** : nouvelle clé JWT dans Vault |
| 9 | Ansible | **preuve** : `GET /tx/auto-probe/status` |
| 10 | `remediation-engine` | publie `RECOVERED_TX` avec la durée |

### 5.2 Pourquoi l'asynchrone

Un playbook réel prend 4 à 8 secondes. Le détecteur appelait ce webhook de façon
synchrone avec un timeout de 5 s et tombait en `ReadTimeout` **alors que la
remédiation fonctionnait**. Panne constatée en recette. `/incident` répond donc
immédiatement et le playbook tourne en tâche de fond ; l'état est publié au fil
de l'exécution via SSE.

### 5.3 La déception active

Le honeypot existait mais **personne ne lui redirigeait le trafic** : l'étape 1
du playbook visait un service inexistant, échouait sous `ignore_errors`, et le
playbook « réussissait » sans rien faire.

`services/waf` corrige cela : reverse proxy qui apprend l'âge des outputs en
proxyfiant `/outputs`, et déroute vers `shadow-api` les transactions dont l'âge
médian des leurres dépasse 8 jours — **uniquement quand la déception est armée
par le playbook**.

La règle de bord est volontairement grossière. Elle ne déclenche rien : seul
`ml-detector` déclenche, seul le playbook arme la déception. Le WAF ne fait
qu'obéir.

**Le test qui compte** : avec la déception armée, une transaction dont les
leurres sont récents passe normalement. Une déception qui casse le paiement du
client serait pire que pas de déception.

### 5.4 Le playbook ne fait plus de théâtre

La version d'origine contenait `docker compose up -d --force-recreate
payment-api` protégé par `ignore_errors`. Le client Docker n'était pas dans
l'image, donc la tâche échouait silencieusement — mais le socket Docker était
monté. À un `apt install` près, elle aurait **effacé le registre d'outputs en
mémoire**, c'est-à-dire la transaction de preuve que la démo doit montrer.

Tâche supprimée, socket retiré. Un redémarrage destructif n'est pas une
remédiation.

---

## 6. Preuves

### 6.1 Répétition générale, trois exécutions consécutives

```
detection_seconds      3,0   3,5   3,7
recovery_seconds       6,6   7,1   7,2
proof_transaction_ok  true  true  true
deception_active      true  true  true
attacker_routed       true  true  true
ansible_rc               0     0     0
chain_complete        true  true  true
```

Limite fixée : 90 s. Marge : **83 secondes** sur un créneau de 3 minutes.

Autrement dit : la reprise complète — détection, playbook Ansible, transaction de
preuve — prend **6,6 à 7,2 secondes** sur trois exécutions consécutives, sans
intervention humaine.

### 6.2 Les quatre familles d'attaques

`tools/attack.py` — lancé depuis le PC Attaquant, via le WAF :

| Attaque | Résultat mesuré |
|---|---|
| `poison` (leurres uniformes) | 12/12 déroutés vers le honeypot, détection 100 % |
| `replay` (même key image) | **409** — anti-double-dépense |
| `burst` (50 ms d'intervalle) | 40 transactions, 9,7 tx/s, aucune erreur |
| `malformed` (anneau vide, clés invalides) | **400** propre, service toujours vivant |

### 6.3 Tests automatisés — 164

| Suite | Nombre | Ce qu'elle prouve |
|---|---|---|
| `remediation-engine` bus d'états | 7 | `seq` croissant, SSE, abonné lent non bloquant |
| `remediation-engine` flux d'incident | 5 | ordre des états, verrou, échec explicite |
| `ml-detector` | 12 | invariance au volume, purge de fenêtre, anti-rebond, réarmement |
| `payment-api` | 13 | gamma, `unspent`, quarantaine, ring-size, PIN, non-exposition des clés |
| `payment-api` (registre) | **8** | journal PostgreSQL, rechargement, repli explicite, écriture groupée |
| `monero-crypto-core` (Rust) | 9 | Pedersen homomorphe, chiffrement réversible, anneau valide, falsifications rejetées |
| `monero-crypto-core` (HTTP) | 10 | émission, dérivation, scan, audit, équilibre, bLSAG, rejeu |
| `waf` | 10 | âge médian, routage honeypot, **client légitime jamais dérouté** |
| `hardware/bridge` | 9 | trame série, états inconnus, pont strictement en lecture |
| `shadow-api` | 4 | honeypot jamais repéré, journal borné |
| `auth-service` | 9 | clé Vault, 503 si Vault muet, cache et rotation |
| `backoffice` | **25** | parcours identifiant → visage → mot de passe, refus et caméra éteinte, blocage 5 min, LED (vert clignotant, vert fixe, rouge ×10, rouge fixe) avec expiration, habilitations, dernier admin protégé, audit sans secret |
| `shop-api` | **21** | catalogue en base, panier borné, achat, stock réservé puis restitué, survente impossible, historique par capability token, administration |
| `rag-assistant` | **14** | index BM25, question de référence → bon document, citations, mode dégradé, disjoncteur Ollama |
| `remediation-engine` (SIEM) | **8** | alerte actionnable → incident, alerte de confort ignorée, anti-rebond, jeton obligatoire |
| `ml-detector` (`/ingest`) | **1** | la télémétrie n'est acceptée qu'avec le jeton de service (évasion impossible) |
| `payment-api` (durcissement) | **3** | anneau borné, administration fermée sans jeton, PIN par défaut refusé |

```bash
./tools/run_tests.sh        # tout
```

### 6.4 Observabilité

Prometheus scrape 12 cibles (toutes `up`), évalue 24 règles d'alerte, et les pousse à Alertmanager → remédiation. Loki évalue 4 règles de **logs** et pousse au même Alertmanager. Grafana est
provisionné (datasource + dashboard 10 panneaux) : aucun clic après `up -d`.

Les alertes ciblent les **pannes silencieuses**, pas les attaques : une attaque
détectée est le comportement attendu du système. On alerte sur le silence —
`shadow_webhook_error`, `shadow_model_ready`, `shadow_crypto_core_ready`,
`shadow_probe_ok`, `shadow_runtime_threshold_ignored`.

---

## 7. Limites

**À lire avant de conclure que tout est prêt.**

### 7.1 Cryptographiques

- **Pas de Bulletproofs** : un montant négatif modulo l satisfait l'équilibre des
  engagements. Suppose un attaquant authentifié qui construit ses propres
  engagements. C'est la seule vraie faiblesse de sécurité du système.
- **bLSAG** et non CLSAG : signature ~2× plus grosse. Aucun impact fonctionnel.

### 7.2 Opérationnelles

- **Registre des outputs** : persisté dans PostgreSQL (`registry.py`, tables
  `outputs` et `key_images`), rechargé au démarrage. Repli mémoire explicite
  si `DATABASE_URL` est absent, publié dans `/healthz`.
- **AppRole Vault non provisionné** : le playbook utilise un token de service,
  pas un `role_id`/`secret_id` à portée restreinte.
- **Modèle calibré sur un seul poste** : 1 129 fenêtres capturées ici. Un seuil
  calibré ailleurs produit des faux positifs — c'est arrivé trois fois.
- **RAG Ollama non branché** : le service RAG est livré (BM25, mode extractif
  l'assistant.
- **Objet physique non flashé**, modèle 3D v1 non imprimé. Non bloquant par
  conception : l'objet n'est jamais nécessaire.
- **Pas de détection de vivacité faciale** : une photo imprimée tenue devant la
  caméra passerait. Publié dans `/healthz` (`liveness: not_implemented`) ; le
  mot de passe reste le second facteur.
- **Back-office en HTTP sur `localhost`** : `getUserMedia` exige un contexte
  sécurisé. Une IP LAN est refusée par le navigateur ; TLS est un chantier
  (`TODO.md` §1.0).
- **Bandeau LED ESP8266 non flashé** : firmware et contrat écrits, recette
  matérielle en 5 tests. L'authentification fonctionne sans lui.

### 7.3 Ce que la démonstration prouve, et ce qu'elle ne prouve pas

**Prouvé** : détection non supervisée avec seuil appris ; remédiation sans
intervention humaine ; preuve de reprise par une transaction réelle ; containment
de l'attaquant ; auditabilité sans capacité de dépense ; stabilité sur trois
exécutions consécutives.

**Non prouvé** : résistance à un attaquant qui possède les clés ; conformité
formelle (pas d'audit externe) ; MFA ; gestion des tiers ; résistance à une
attaque par déni de service volumétrique.

---

## 8. Enseignements

**Le risque dominant n'est pas l'attaque, c'est la panne silencieuse.** Sur les
11 défauts corrigés pendant le projet, **aucun** n'était une attaque réussie.
Tous venaient d'une erreur d'exploitation ou d'une dépendance matérielle :

| Défaut | Ce qu'il produisait |
|---|---|
| Image sans modèle ML | détecteur en `degraded`, aucune détection, aucune erreur |
| Seuil de calibration périmé | score d'attaque sous le seuil → silence total |
| Webhook vers un hôte inexistant | aucun incident n'arrivait au moteur |
| Verrou jamais réarmé | la **deuxième** attaque ne déclenchait plus rien |
| Playbook dans la requête HTTP | timeout du détecteur alors que la remédiation marchait |
| `UNDER_ATTACK` écrasé en 3 ms | le rouge n'était jamais visible |
| Fenêtre non purgée | deux démos consécutives se contaminaient |
| Tâche Ansible décorative | playbook « réussi » sans rien corriger |

La conclusion d'ingénierie : **un système qui échoue bruyamment vaut mieux qu'un
système qui réussit en silence.** C'est pourquoi chaque panne connue a produit
un contrôle bloquant dans `e2e_demo.py`, une métrique dans `/metrics`, ou une
alerte Prometheus.

---

## 9. Pour aller plus loin

Détail complet des chantiers restants, avec critères d'acceptation :
[`TODO.md`](../TODO.md).

Journal technique des trois sessions, avec les mesures et les erreurs :
[`COMPTE-RENDU.md`](COMPTE-RENDU.md).

Analyse de risques, PCA/PRA, NIS2/DORA :
[`GOUVERNANCE.md`](GOUVERNANCE.md).

Installation du poste et recette :
[`INSTALLATION-LOCAL.md`](INSTALLATION-LOCAL.md).

Runbook de soutenance :
[`DEMO.md`](DEMO.md).

---

## Annexe A — Inventaire des endpoints

| Service | Port | Routes |
|---|---|---|
| `monero-crypto-core` | 8081 | `/healthz`, `/metrics`, `/crypto/{keys,stealth-address,mint-outputs,derive-one-time-key,check-output,audit-view,commit,commit-split,commitment-balance,ring-sign,ring-verify}` |
| `payment-api` | 8080 | `/healthz`, `/metrics`, `/outputs`, `/decoys`, `/wallet/public`, `/tx/submit`, `/tx/auto-probe/status`, `/pin/verify`, `/pin/stats`, `/audit/last`, `/audit/outputs/{id}`, `/admin/{quarantine-outputs,ring-size}` |
| `auth-service` | 8082 | `/healthz`, `/metrics`, `/auth/login` |
| `ml-detector` | 8083 | `/healthz`, `/metrics`, `/ingest`, `/score`, `/threshold` |
| `remediation-engine` | 8084 | `/healthz`, `/metrics`, `/incident`, `/state`, `/state/report`, `/events` (SSE), `/history` |
| `waf` | 8085 | `/healthz`, `/metrics`, `/admin/{redirect,reset,state}`, proxy `/*` |
| `shadow-api` | 8090 | `/healthz`, `/metrics`, `/decoys`, `/tx/submit`, `/admin/interceptions` |
| `shop-api` | 8087 | `/healthz`, `/metrics`, `/products`, `/cart`, `/checkout`, `/orders`, `/admin/{products,orders}` |
| `rag-assistant` | 8091 | `/healthz`, `/metrics`, `/ask` |
| `backoffice` | 8086 | `/healthz`, `/metrics`, `/led/state`, `/auth/{bootstrap,identify/start,identify,password,me,logout}`, `/admin/{users,users/{id},users/{id}/face,users/{id}/unlock,audit,sessions,sessions/{id},stats}` |

## Annexe B — Reproduire les mesures

```bash
# Chaîne complète
python tools/e2e_demo.py

# Validation par le vrai chemin de production
cd services/ml-detector && ../../.venv/bin/python validate_real.py --n 20

# Séparation des features (AUC)
cd services/ml-detector && ../../.venv/bin/python analyze_separation.py

# Les 4 familles d'attaques
python tools/attack.py all

# Tous les tests
./tools/run_tests.sh
```

## Annexe C — Volumétrie

| Élément | Valeur |
|---|---|
| Services applicatifs | 11 (dont 1 en Rust) |
| Conteneurs | 19 |
| Lignes de code (Python, Rust, JSX, C++, firmware) | ~13 300 |
| Routes HTTP | ~100 (dont 13 en Rust, 21 back-office, 14 boutique) |
| Tests automatisés | 164 |
| Fenêtres d'entraînement | 1 129 |
| Durée de la chaîne P0 | 6,6 à 7,2 s (limite 90 s) |
