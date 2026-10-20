# Dossier d'ingÃ©nierie â€” RESILIENCE-ALPHA / SHADOW-SETTLE

**Workshop M2 â€” 2026**
SystÃ¨me de paiement fictif Ã  confidentialitÃ© cryptographique, avec dÃ©tection
d'anomalies par apprentissage automatique et remÃ©diation autonome.

> Ce dossier dÃ©crit le systÃ¨me **tel qu'il tourne**, pas tel qu'il pourrait
> tourner. Chaque affirmation renvoie Ã  un fichier, un endpoint ou une mesure
> reproductible. Les limites sont au Â§7, et elles sont assumÃ©es.

---

## 1. Le problÃ¨me traitÃ©

Le sujet impose quatre contraintes qui, ensemble, interdisent les solutions
habituelles :

1. **Une attaque rÃ©elle**, lancÃ©e depuis un poste distinct.
2. **Une dÃ©tection par machine learning**, explicitement Â« et non par de simples
   seuils fixes Â» â€” un seuil codÃ© en dur est Ã©liminatoire dans l'esprit de la
   grille.
3. **Une remÃ©diation automatique** : toute intervention humaine pendant la
   dÃ©monstration donne 0/5 sur l'axe principal.
4. **Une preuve** : une nouvelle transaction doit rÃ©ussir aprÃ¨s l'incident.

La difficultÃ© n'est pas de faire chacune de ces choses. C'est de les enchaÃ®ner
**en moins de 3 minutes, sans intervention, de faÃ§on rÃ©pÃ©table**. Une dÃ©mo qui
marche une fois sur deux vaut zÃ©ro point.

Notre rÃ©ponse tient en une phrase : *rendre l'attaque physiquement visible dans
les donnÃ©es, apprendre le seuil qui la sÃ©pare du trafic normal, et faire porter
la preuve de reprise par une transaction rÃ©elle plutÃ´t que par un log.*

---

## 2. Architecture

### 2.1 Vue d'ensemble

```
   PC Attaquant                     PC HÃ´te (docker compose, 15 services)
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ attack.pyâ”‚â”€â”€â–º waf:8085 â”€â”€â”¬â”€â”€â–º payment-api:8080 â”€â”€â–º monero-crypto-core:8081
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   (proxy)     â”‚         â”‚                        (Rust)
                              â”‚         â”‚ tÃ©lÃ©mÃ©trie
                              â”‚         â–¼
                              â”‚    ml-detector:8083
                              â”‚         â”‚ POST /incident
                              â”‚         â–¼
                              â”‚    remediation-engine:8084
                              â”‚         â”‚ ansible-playbook
                              â”‚         â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–º waf:8085/admin/redirect
                              â”‚         â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–º payment-api:8080/admin/*
                              â”‚         â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–º vault:8200 (rotation clÃ©)
                              â”‚         â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–º SSE /events
                              â”‚                        â”‚
                              â”‚              â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                              â””â”€â”€â–º shadow-api â”‚ front-dashboard   â”‚ hardware_bridge
                                   (honeypot) â”‚ (anneau + score)  â”‚ (ESP32, hÃ´te)
                                              â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                                        â”‚ session (Bearer)
                                              â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   camÃ©ra USB â”€â”€â”€â”€ image JPEG â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–ºâ”‚ backoffice:8086   â”‚
   (PC hÃ´te, navigateur)                      â”‚ YuNet + SFace     â”‚
                                              â”‚ SQLite + audit    â”‚
                                              â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                                        â”‚ GET /led/state (WiFi)
                                              â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                                              â”‚ ESP8266 + LED RGB â”‚
                                              â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### 2.2 Les cinq piliers, et ce qui les relie rÃ©ellement

| Pilier | Composants | Lien avec les autres |
|---|---|---|
| **DEV** | `payment-api`, `monero-crypto-core`, `front-dashboard` | le dashboard consomme SSE et `/score` ; l'API appelle le noyau Rust Ã  chaque transaction |
| **INFRA** | `docker compose`, `ansible/`, `prometheus`, `grafana`, `loki` | le moteur exÃ©cute un vrai playbook Ansible ; Prometheus scrape 9 services |
| **IA/DATA** | `ml-detector` (scikit-learn + PyTorch) | publie l'incident au moteur ; expose le score au dashboard |
| **CYBER** | `waf`, `shadow-api`, `auth-service`, `vault`, **`backoffice`** | le WAF dÃ©route vers le honeypot ; Vault dÃ©tient la clÃ© JWT ; Ansible fait tourner cette clÃ© ; le back-office contrÃ´le l'accÃ¨s au tableau de supervision |
| **TRANSFORMATION** | `docs/GOUVERNANCE.md` | EBIOS RM et PCA/PRA reliÃ©s aux composants ci-dessus, pas Ã  un schÃ©ma thÃ©orique |

Un pilier n'est pas Â« prÃ©sent Â» parce qu'un conteneur porte son nom. Il l'est
quand un flux le traverse. C'est ce que vÃ©rifie `tools/e2e_demo.py`.

### 2.3 DÃ©cisions structurantes

**Une seule source de vÃ©ritÃ© pour l'Ã©tat.** `remediation-engine` publie
(`StateBus`), le dashboard et le terminal consomment. Aucun des deux ne
recalcule l'Ã©tat. Deux logiques parallÃ¨les finiraient par diverger le jour de la
dÃ©monstration.

**Le terminal physique ne peut pas casser la dÃ©mo.** Aucun abonnÃ© SSE n'est
obligatoire ; le pont est strictement en lecture ; aucun chemin ne va du
terminal vers le moteur. Un test vÃ©rifie l'absence de POST dans le pont.

**La dÃ©tection est hors du chemin critique.** Si `ml-detector` tombe, le
paiement continue. C'est volontaire : le ML observe, il ne filtre pas.

**Le WAF est le seul point d'entrÃ©e Â« client Â».** L'attaquant y passe, le client
lÃ©gitime aussi â€” mais seule la transaction qui porte le signal d'attaque est
dÃ©routÃ©e. Une dÃ©ception qui casse le paiement du client serait pire que pas de
dÃ©ception.

### 2.4 L'authentification du back-office (consigne additionnelle)

Le tableau de supervision n'est plus accessible sans franchir trois Ã©tapes :
identifiant, **reconnaissance faciale sur la camÃ©ra USB du PC hÃ´te**, mot de
passe. Un bandeau LED ESP8266 rend chaque Ã©tape visible Ã  distance.

```
 navigateur (PC hÃ´te)                 docker (PC hÃ´te)
 â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   JPEG    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
 â”‚ getUserMedia       â”‚â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–ºâ”‚ backoffice:8086              â”‚
 â”‚ camÃ©ra USB         â”‚           â”‚  YuNet â†’ SFace â†’ cosinus     â”‚
 â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜           â”‚  SQLite : comptes, sessions,  â”‚
          â–²                       â”‚  blocages, audit             â”‚
          â”‚ Ã©tat                  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
 â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   GET /led/state (300 ms) â”‚
 â”‚ ESP8266 + LED RGB â”‚â—„â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
 â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

Trois dÃ©cisions qui mÃ©ritent une phrase en Q&A :

**La dÃ©cision de sÃ©curitÃ© est cÃ´tÃ© serveur.** Le navigateur n'annonce jamais
Â« visage reconnu Â» : il envoie une image, le serveur calcule le descripteur et
le compare. Un client modifiÃ© ne peut pas sauter l'Ã©tape 2. Le compteur
d'Ã©checs et le blocage de 5 minutes vivent en base : recharger la page ne les
remet pas Ã  zÃ©ro.

**L'ESP interroge, il n'est pas poussÃ©.** Le PC hÃ´te est le point d'accÃ¨s : la
carte est derriÃ¨re son NAT. Un POST serveur â†’ carte exigerait une IP fixe, une
rÃ©servation DHCP et une rÃ¨gle de pare-feu entrante â€” trois causes de panne pour
300 ms de latence gagnÃ©es. Le firmware reste maÃ®tre du temps (30 s de vert fixe,
10 flashs rouges) et **s'Ã©teint s'il ne joint plus le back-office** : pas de feu
vert sans service.

**Le visage est un facteur d'identitÃ©, pas un anti-spoofing.** Une photo
imprimÃ©e passerait ; c'est publiÃ© dans `/healthz` (`liveness: not_implemented`).
Le mot de passe reste le second facteur, et il n'est demandÃ© qu'aprÃ¨s la
reconnaissance.

CoÃ»t mesurÃ© : **29 ms** par visage sur un CPU portable, 38 Mo de modÃ¨les
tÃ©lÃ©chargÃ©s et vÃ©rifiÃ©s par SHA-256 **Ã  la construction de l'image** â€” aucun
tÃ©lÃ©chargement au runtime, donc aucun Ã©chec rÃ©seau possible pendant la
dÃ©monstration.

### 2.5 La boutique : un site vitrine, pas un tableau de bord

Les rÃ¨gles du jury du 20/10 dÃ©placent le centre de gravitÃ© : le SI doit Ãªtre un
**site de paiement** avec des articles en base, un front, un back, des commandes
et un historique. `services/shop-api` (PostgreSQL, port 8087) le porte.

| Ã‰lÃ©ment | RÃ©alisation |
|---|---|
| Articles | table `products` (SKU, prix, stock, actif), catalogue de 6 articles amorcÃ© une fois |
| Panier | tables `carts` / `cart_items`, quantitÃ© bornÃ©e (1..20) |
| Commande | `orders` + `order_items` (photo du prix au moment de l'achat) |
| Paiement | `payments` + `payment_events`, statut `pending â†’ paid | failed` |
| Preuve | `payment-api /tx/pay` : output du **montant exact**, signature d'anneau bLSAG, key image |
| Historique | `GET /orders` par **capability token** (`customer_ref` 256 bits) |
| Back | `AdminShop.jsx` : prix, stock, retrait, commandes et preuve |

Trois dÃ©cisions qui comptent :

**Le stock est rÃ©servÃ© dans une transaction, puis restituÃ© si le paiement
Ã©choue.** Un `UPDATE products SET stock = stock - ? WHERE stock >= ?` dont le
nombre de lignes affectÃ©es est vÃ©rifiÃ© : deux acheteurs simultanÃ©s sur le
dernier article donnent une commande et un `409`, jamais un stock nÃ©gatif. Un
Ã©chec de paiement restaure le stock et laisse le panier intact.

**Une commande n'est `paid` qu'aprÃ¨s une preuve cryptographique.** Le montant
vient du serveur, jamais du client ; la key image renvoyÃ©e par le noyau est
stockÃ©e dans `payments`. Un paiement refusÃ© produit une commande `failed` avec
son motif dans `payment_events` â€” visible par le client et par l'administrateur.

**L'administration n'utilise pas le jeton de service.** Le navigateur envoie la
session opaque du back-office ; `shop-api` la valide auprÃ¨s du back-office et
exige le rÃ´le `admin`. Le jeton de service reste serveur-Ã -serveur.

### 2.6 L'IA : RAG et ingestion SIEM

Deux briques rÃ©pondent Ã  Â« l'IA actualise ses compÃ©tences via le RAG Â» et
Â« l'IA gÃ¨re les alertes des outils de supervision de sorte Ã  agir elle-mÃªme Â».

**RAG (`services/rag-assistant`, 8091).** Index BM25 construit **Ã  la
construction de l'image** sur `docs/` et `README.md` : 13 documents, 332
passages. `POST /ask` renvoie une rÃ©ponse **avec ses sources**. Si Ollama
(`llama3.1:8b`) est joignable, il rÃ©dige Ã  partir des passages ; sinon le
service rÃ©pond en **extractif** â€” les phrases du dossier, citÃ©es â€” et le dit
dans `/healthz` (`generation.ollama: unreachable`). Un assistant qui invente
serait pire qu'un assistant qui cite.

**Ingestion SIEM (`POST /alert`).** Prometheus Ã©value 20 rÃ¨gles, Alertmanager
les pousse au moteur de remÃ©diation. Une liste **fermÃ©e** d'alertes est
actionnable (`NoyauCryptoIndisponible`, `SondeTransactionEnEchec`,
`WafIndisponible`â€¦) ; les alertes de confort (bandeau LED, compte bloquÃ©) sont
journalisÃ©es sans ouvrir d'incident. Le moteur rÃ©utilise **exactement** le
chemin de l'incident ML : mÃªme verrou, mÃªme playbook, mÃªme preuve de reprise.
Deux chemins de remÃ©diation finiraient par diverger.

---

## 3. La dÃ©tection : apprendre, pas seuiller

### 3.1 Le pipeline

```
payment-api â”€â”€observations brutesâ”€â”€â–º ml-detector
   (faits)                          â”œâ”€â”€ features.py  (6 features, 1 seule dÃ©finition)
                                    â”œâ”€â”€ StandardScaler   (appris)
                                    â”œâ”€â”€ IsolationForest  (appris)
                                    â”œâ”€â”€ Autoencoder 6-4-2-4-6 (appris)
                                    â””â”€â”€ seuil appris â†’ threshold.json
```

`features.py` est **partagÃ©** entre l'entraÃ®nement et l'infÃ©rence. C'Ã©tait le
dÃ©faut majeur du squelette : entraÃ®nement sur des distributions inventÃ©es
(`cpu=4000 Âµs`, `entropie=3,2`), infÃ©rence sur des zÃ©ros. Le modÃ¨le ne pouvait
pas fonctionner, quel que soit le seuil.

Les 6 features :

| # | Feature | Ce qu'elle mesure |
|---|---|---|
| 0 | `decoy_median_age_days` | Ã¢ge mÃ©dian des leurres de l'anneau |
| 1 | `ring_verify_cpu_us` | coÃ»t de la vÃ©rification cryptographique |
| 2 | `key_image_rejection_ratio` | taux de rejets (double dÃ©pense, PIN) |
| 3 | `inter_arrival_median` | cadence mÃ©diane des requÃªtes |
| 4 | `ring_age_skewness` | asymÃ©trie des Ã¢ges de l'anneau |
| 5 | `tx_latency_p95` | latence perÃ§ue par le client |

### 3.2 Ce que la mesure a imposÃ© de changer

Quatre erreurs de conception ont Ã©tÃ© trouvÃ©es **par la mesure**, pas Ã  la
lecture du code. Chacune produisait des faux positifs massifs.

| DÃ©faut | SymptÃ´me observÃ© | Correction |
|---|---|---|
| Entropie calculÃ©e sur les Ã¢ges **poolÃ©s** de la fenÃªtre | 3,0 bits Ã  10 Ã©chantillons, 5,4 bits Ã  160, **Ã  distribution identique** : la feature mesurait la cadence | Calcul **par Ã©vÃ©nement** puis moyenne |
| `inter_arrival` en Ã©cart-type | Une pause de 6 s suffisait Ã  produire un score de 6,2 | **MÃ©diane** des intervalles |
| Sonde de liveness dans la tÃ©lÃ©mÃ©trie | Cadence fixe de 4 s, pool de leurres qui dÃ©rivait | Champ `synthetic`, exclu du calcul |
| Entropie comme feature principale | AUC 0,56 : elle sature Ã  10 leurres par transaction | RemplacÃ©e par l'**Ã¢ge mÃ©dian des leurres** (AUC 1,00) |

Avant ces corrections : 70 Ã  100 % de faux dÃ©clenchements sur du trafic
parfaitement normal. AprÃ¨s : voir Â§3.4.

### 3.3 Pourquoi l'Ã¢ge mÃ©dian des leurres

Monero tire ses leurres selon une **gamma biaisÃ©e vers les outputs rÃ©cents** :
un wallet ne pioche pas uniformÃ©ment dans le registre. Un attaquant qui veut
empoisonner l'anneau n'a pas accÃ¨s Ã  cette distribution â€” il lit le registre
public et tire uniformÃ©ment.

MesurÃ© sur ce systÃ¨me (`analyze_separation.py`, 1 173 fenÃªtres par classe) :

| Feature | Normal | Attaque | AUC |
|---|---|---|---|
| `decoy_median_age_days` | **3,850 Â± 0,167** | **25,731 Â± 1,859** | **1,000** |
| `ring_age_skewness` | 0,934 Â± 0,191 | 1,456 Â± 0,226 | 0,947 |
| `ring_verify_cpu_us` | 2 502 Â± 573 | idem | 0,500 |
| `inter_arrival_median` | 0,356 Â± 0,356 | idem | 0,500 |
| `tx_latency_p95` | 46,10 Â± 12,76 | idem | 0,500 |
| `key_image_rejection_ratio` | 0 | 0 | 0,500 |

**Lecture honnÃªte** : une seule feature discrimine l'attaque principale. Les
autres mesurent des attaques *diffÃ©rentes* (rejeu de key image, rafale
automatisÃ©e, anneau malformÃ©) et restent dans le vecteur pour cette raison â€” un
modÃ¨le Ã  une seule feature ne dÃ©tecterait qu'une attaque. C'est un choix
dÃ©fendable, Ã  condition de le dire.

### 3.4 Le seuil, et pourquoi il a changÃ© deux fois

Le seuil est **appris**, jamais codÃ© en dur. Il est le maximum de deux
estimateurs calculÃ©s sur un jeu de calibration **hors Ã©chantillon** (25 % de la
baseline, jamais vus Ã  l'entraÃ®nement).

Sur 1 129 fenÃªtres rÃ©elles, nous avons comparÃ© les estimateurs :

| Estimateur | Seuil | FPR | FPR sur 3 fenÃªtres consÃ©cutives |
|---|---|---|---|
| moyenne + 4Ïƒ | 2,97 | 1,42 % | **0,89 %** |
| 99,5áµ‰ percentile | 3,44 | 0,53 % | 0,18 % |
| moyenne + 6Ïƒ | 4,24 | 0,27 % | 0,09 % |
| **99,9áµ‰ percentile + 6Ïƒ** | **4,61** | **0,27 %** | **0,09 %** |

La rÃ¨gle initiale (`max(p99.5, m+4Ïƒ)`) donnait 0,89 % de dÃ©clenchements sur
trois fenÃªtres consÃ©cutives : un faux positif toutes les ~2 minutes de trafic
dense. Inacceptable pour une dÃ©mo de 3 minutes. Le seuil retenu est le max du
99,9áµ‰ percentile et de `moyenne + 6Ïƒ`.

Le coÃ»t des deux erreurs n'est pas symÃ©trique : un faux dÃ©clenchement pendant la
dÃ©mo coÃ»te 25 points, une dÃ©tection 2 s plus tardive coÃ»te 2 secondes. Et la
marge est Ã©norme (Â§3.5), donc on peut Ãªtre conservateur sans rien perdre.

**Anti-rebond** : 3 fenÃªtres consÃ©cutives au-dessus du seuil sont exigÃ©es. Une
fenÃªtre isolÃ©e ne dÃ©clenche rien.

**Refus de conclure** : sous 8 Ã©vÃ©nements dans la fenÃªtre, le dÃ©tecteur reste en
`warming` et ne produit aucun score. Mieux vaut ne rien affirmer que produire un
faux positif sur du bruit d'Ã©chantillonnage.

### 3.5 RÃ©sultats mesurÃ©s

Validation par le **vrai chemin de production** (`validate_real.py` : trafic
lÃ©gitime et attaque envoyÃ©s via `payment-api`, aucun montage de test) :

```
seuil                4,609
trafic normal        moyenne 0,80   max 1,24
attaque              moyenne 14,62  min 14,29
faux positifs        0 %            (0 dÃ©clenchement)
dÃ©tection            100 %
```

Le score le plus haut du trafic normal est **3,7Ã— sous** le seuil ; le score le
plus bas de l'attaque est **3,1Ã— au-dessus**. Aucun chevauchement.

---

## 4. La cryptographie

### 4.1 Ce qui est rÃ©ellement implÃ©mentÃ©

`monero-crypto-core` (Rust, Ristretto255) expose 13 routes. Un modÃ¨le Monero
simplifiÃ©, mais dont **aucune primitive de sÃ©curitÃ© n'est simulÃ©e** :

| Primitive | Formule | RÃ´le |
|---|---|---|
| ClÃ©s | `A = aG`, `B = bG` | View Key / Spend Key sÃ©parÃ©es |
| Adresse one-time | `P = H_s(rA)G + B`, `R = rG` | une adresse par transaction |
| ClÃ© dÃ©rivÃ©e porteur | `x = H_s(aR) + b` | seul le porteur peut dÃ©penser |
| **Key image** | `I = xÂ·H_p(P)` | anti-double-dÃ©pense |
| **Signature d'anneau** | bLSAG sur `n` clÃ©s, message liÃ© | prouve la possession sans rÃ©vÃ©ler laquelle |
| Engagements | `C = aG + rH`, `H` sans log discret connu | montants cachÃ©s, homomorphes |
| Ã‰quilibre | `âˆ‘C_in âˆ’ âˆ‘C_out = 0` | conservation de la valeur |
| Montant | masque `Keccak("amount" â€– shared)` | confidentialitÃ© |
| Audit | `GET /audit/last` avec la **seule** View Key | conformitÃ© DORA art. 19 |

### 4.2 Le dÃ©tail qui fait la diffÃ©rence

La key image utilise un **hash-vers-le-groupe** (`SHA-512` â†’
`RistrettoPoint::from_uniform_bytes`, projection uniforme de type Elligator).

La tentation naturelle est de dÃ©finir `H_p(P) = h(P)Â·G` avec `h` un hash vers un
scalaire. C'est plus simple Ã  Ã©crire, et **Ã§a dÃ©truit l'anti-double-dÃ©pense** :
n'importe qui peut choisir une key image arbitraire `I = zG`, puis calculer
`R_Ï€ = (uÂ·h + c_Ï€Â·z)G` et produire une signature valide pour un output qu'il ne
possÃ¨de pas. L'anti-double-dÃ©pense devient dÃ©coratif.

Avec un vrai hash-vers-le-groupe, personne ne connaÃ®t le logarithme discret de
`H_p(P)` â€” y compris nous. La key image ne peut Ãªtre produite qu'en connaissant
`x`. C'est le genre d'erreur qui ne se voit pas Ã  la lecture et qui dÃ©truit la
crÃ©dibilitÃ© technique du projet en Q&A.

### 4.3 L'audit view-only

La fonction de gouvernance : un auditeur qui dÃ©tient **uniquement la View Key**
peut lire le montant d'une transaction, mais ne peut pas la signer, donc pas la
dÃ©penser. C'est une sÃ©paration mathÃ©matique, pas une sÃ©paration de droits
logiciels.

```bash
curl -s localhost:8080/audit/last
# {"available": true, "amount": 323, "capability": "view-only (aucune capacitÃ© de signature)"}
```

### 4.4 Ã‰carts assumÃ©s avec Monero de production

| Ã‰cart | ConsÃ©quence | Statut |
|---|---|---|
| **bLSAG** au lieu de **CLSAG** | signature ~2Ã— plus grosse (linÃ©aire en N) | mÃªme modÃ¨le de sÃ©curitÃ©, optimisation de taille non faite |
| **Pas de Bulletproofs** | un montant nÃ©gatif modulo l satisfait `âˆ‘C = 0` | **vraie faiblesse**, publiÃ©e dans `/healthz` |
| Pas de mnÃ©monique | les clÃ©s sont des scalaires, pas une phrase | hors pÃ©rimÃ¨tre dÃ©mo |
| Registre en mÃ©moire | perdu au redÃ©marrage | assumÃ© (cf. Â§7) |

Ces trois lignes sont dans `GET /healthz` (`not_implemented`) et dans le dossier
de gouvernance. Un systÃ¨me qui prÃ©tend ne pas avoir de limites perd toute
crÃ©dibilitÃ© au premier contrÃ´le.

---

## 5. La remÃ©diation et la dÃ©ception

### 5.1 SÃ©quence complÃ¨te, sans intervention

| Ã‰tape | Composant | Action rÃ©elle |
|---|---|---|
| 1 | `ml-detector` | 3 fenÃªtres anormales â†’ `POST /incident` |
| 2 | `remediation-engine` | rÃ©pond 202 immÃ©diatement, publie `UNDER_ATTACK` |
| 3 | (attente 3 s) | le rouge est visible Ã  l'Ã©cran et sur l'objet |
| 4 | `remediation-engine` | publie `REMEDIATING`, lance `ansible-playbook` |
| 5 | Ansible | **dÃ©ception** : `POST waf:8085/admin/redirect` |
| 6 | Ansible | **quarantaine** : `POST payment-api/admin/quarantine-outputs` (25 plus anciens) |
| 7 | Ansible | **durcissement** : `POST payment-api/admin/ring-size` (11 â†’ 25) |
| 8 | Ansible | **rotation** : nouvelle clÃ© JWT dans Vault |
| 9 | Ansible | **preuve** : `GET /tx/auto-probe/status` |
| 10 | `remediation-engine` | publie `RECOVERED_TX` avec la durÃ©e |

### 5.2 Pourquoi l'asynchrone

Un playbook rÃ©el prend 4 Ã  8 secondes. Le dÃ©tecteur appelait ce webhook de faÃ§on
synchrone avec un timeout de 5 s et tombait en `ReadTimeout` **alors que la
remÃ©diation fonctionnait**. Panne constatÃ©e en recette. `/incident` rÃ©pond donc
immÃ©diatement et le playbook tourne en tÃ¢che de fond ; l'Ã©tat est publiÃ© au fil
de l'exÃ©cution via SSE.

### 5.3 La dÃ©ception active

Le honeypot existait mais **personne ne lui redirigeait le trafic** : l'Ã©tape 1
du playbook visait un service inexistant, Ã©chouait sous `ignore_errors`, et le
playbook Â« rÃ©ussissait Â» sans rien faire.

`services/waf` corrige cela : reverse proxy qui apprend l'Ã¢ge des outputs en
proxyfiant `/outputs`, et dÃ©route vers `shadow-api` les transactions dont l'Ã¢ge
mÃ©dian des leurres dÃ©passe 8 jours â€” **uniquement quand la dÃ©ception est armÃ©e
par le playbook**.

La rÃ¨gle de bord est volontairement grossiÃ¨re. Elle ne dÃ©clenche rien : seul
`ml-detector` dÃ©clenche, seul le playbook arme la dÃ©ception. Le WAF ne fait
qu'obÃ©ir.

**Le test qui compte** : avec la dÃ©ception armÃ©e, une transaction dont les
leurres sont rÃ©cents passe normalement. Une dÃ©ception qui casse le paiement du
client serait pire que pas de dÃ©ception.

### 5.4 Le playbook ne fait plus de thÃ©Ã¢tre

La version d'origine contenait `docker compose up -d --force-recreate
payment-api` protÃ©gÃ© par `ignore_errors`. Le client Docker n'Ã©tait pas dans
l'image, donc la tÃ¢che Ã©chouait silencieusement â€” mais le socket Docker Ã©tait
montÃ©. Ã€ un `apt install` prÃ¨s, elle aurait **effacÃ© le registre d'outputs en
mÃ©moire**, c'est-Ã -dire la transaction de preuve que la dÃ©mo doit montrer.

TÃ¢che supprimÃ©e, socket retirÃ©. Un redÃ©marrage destructif n'est pas une
remÃ©diation.

---

## 6. Preuves

### 6.1 RÃ©pÃ©tition gÃ©nÃ©rale, trois exÃ©cutions consÃ©cutives

```
detection_seconds      3,0   3,5   3,7
recovery_seconds       6,6   7,1   7,2
proof_transaction_ok  true  true  true
deception_active      true  true  true
attacker_routed       true  true  true
ansible_rc               0     0     0
chain_complete        true  true  true
```

Limite fixÃ©e : 90 s. Marge : **83 secondes** sur un crÃ©neau de 3 minutes.

### 6.2 Les quatre familles d'attaques

`tools/attack.py` â€” lancÃ© depuis le PC Attaquant, via le WAF :

| Attaque | RÃ©sultat mesurÃ© |
|---|---|
| `poison` (leurres uniformes) | 12/12 dÃ©routÃ©s vers le honeypot, dÃ©tection 100 % |
| `replay` (mÃªme key image) | **409** â€” anti-double-dÃ©pense |
| `burst` (50 ms d'intervalle) | 40 transactions, 9,7 tx/s, aucune erreur |
| `malformed` (anneau vide, clÃ©s invalides) | **400** propre, service toujours vivant |

### 6.3 Tests automatisÃ©s â€” 141

| Suite | Nombre | Ce qu'elle prouve |
|---|---|---|
| `remediation-engine` bus d'Ã©tats | 7 | `seq` croissant, SSE, abonnÃ© lent non bloquant |
| `remediation-engine` flux d'incident | 5 | ordre des Ã©tats, verrou, Ã©chec explicite |
| `ml-detector` | 11 | invariance au volume, purge de fenÃªtre, anti-rebond, rÃ©armement |
| `payment-api` | 10 | gamma, `unspent`, quarantaine, ring-size, PIN, non-exposition des clÃ©s |
| `monero-crypto-core` (Rust) | 8 | Pedersen homomorphe, chiffrement rÃ©versible, anneau valide, falsifications rejetÃ©es |
| `monero-crypto-core` (HTTP) | 10 | Ã©mission, dÃ©rivation, scan, audit, Ã©quilibre, bLSAG, rejeu |
| `waf` | 10 | Ã¢ge mÃ©dian, routage honeypot, **client lÃ©gitime jamais dÃ©routÃ©** |
| `hardware/bridge` | 9 | trame sÃ©rie, Ã©tats inconnus, pont strictement en lecture |
| `shadow-api` | 4 | honeypot jamais repÃ©rÃ©, journal bornÃ© |
| `auth-service` | 5 | clÃ© Vault, 503 si Vault muet, cache et rotation |
| `backoffice` | **25** | parcours identifiant â†’ visage â†’ mot de passe, refus et camÃ©ra Ã©teinte, blocage 5 min, LED (vert clignotant, vert fixe, rouge Ã—10, rouge fixe) avec expiration, habilitations, dernier admin protÃ©gÃ©, audit sans secret |
| `shop-api` | **17** | catalogue en base, panier bornÃ©, achat, stock rÃ©servÃ© puis restituÃ©, survente impossible, historique par capability token, administration |
| `rag-assistant` | **10** | index BM25, question de rÃ©fÃ©rence â†’ bon document, citations, mode dÃ©gradÃ© |
| `remediation-engine` (SIEM) | **6** | alerte actionnable â†’ incident, alerte de confort ignorÃ©e, anti-rebond, jeton obligatoire |

```bash
./tools/run_tests.sh        # tout
```

### 6.4 ObservabilitÃ©

Prometheus scrape 11 cibles (toutes `up`), Ã©value 20 rÃ¨gles d'alerte, et les pousse Ã  Alertmanager â†’ remÃ©diation. Grafana est
provisionnÃ© (datasource + dashboard 10 panneaux) : aucun clic aprÃ¨s `up -d`.

Les alertes ciblent les **pannes silencieuses**, pas les attaques : une attaque
dÃ©tectÃ©e est le comportement attendu du systÃ¨me. On alerte sur le silence â€”
`shadow_webhook_error`, `shadow_model_ready`, `shadow_crypto_core_ready`,
`shadow_probe_ok`, `shadow_runtime_threshold_ignored`.

---

## 7. Limites

**Ã€ lire avant de conclure que tout est prÃªt.**

### 7.1 Cryptographiques

- **Pas de Bulletproofs** : un montant nÃ©gatif modulo l satisfait l'Ã©quilibre des
  engagements. Suppose un attaquant authentifiÃ© qui construit ses propres
  engagements. C'est la seule vraie faiblesse de sÃ©curitÃ© du systÃ¨me.
- **bLSAG** et non CLSAG : signature ~2Ã— plus grosse. Aucun impact fonctionnel.

### 7.2 OpÃ©rationnelles

- **Registre des outputs en mÃ©moire** : perdu au redÃ©marrage de `payment-api`.
  PostgreSQL est prÃ©sent dans la stack mais n'est pas utilisÃ© pour cela.
- **AppRole Vault non provisionnÃ©** : le playbook utilise un token de service,
  pas un `role_id`/`secret_id` Ã  portÃ©e restreinte.
- **ModÃ¨le calibrÃ© sur un seul poste** : 1 129 fenÃªtres capturÃ©es ici. Un seuil
  calibrÃ© ailleurs produit des faux positifs â€” c'est arrivÃ© trois fois.
- **RAG Ollama non commencÃ©** : le pilier IA/DATA est couvert par le ML, pas par
  l'assistant.
- **Objet physique non flashÃ©**, modÃ¨le 3D v1 non imprimÃ©. Non bloquant par
  conception : l'objet n'est jamais nÃ©cessaire.
- **Pas de dÃ©tection de vivacitÃ© faciale** : une photo imprimÃ©e tenue devant la
  camÃ©ra passerait. PubliÃ© dans `/healthz` (`liveness: not_implemented`) ; le
  mot de passe reste le second facteur.
- **Back-office en HTTP sur `localhost`** : `getUserMedia` exige un contexte
  sÃ©curisÃ©. Une IP LAN est refusÃ©e par le navigateur ; TLS est un chantier
  (`TODO.md` Â§1.0).
- **Bandeau LED ESP8266 non flashÃ©** : firmware et contrat Ã©crits, recette
  matÃ©rielle en 5 tests. L'authentification fonctionne sans lui.

### 7.3 Ce que la dÃ©monstration prouve, et ce qu'elle ne prouve pas

**ProuvÃ©** : dÃ©tection non supervisÃ©e avec seuil appris ; remÃ©diation sans
intervention humaine ; preuve de reprise par une transaction rÃ©elle ; containment
de l'attaquant ; auditabilitÃ© sans capacitÃ© de dÃ©pense ; stabilitÃ© sur trois
exÃ©cutions consÃ©cutives.

**Non prouvÃ©** : rÃ©sistance Ã  un attaquant qui possÃ¨de les clÃ©s ; conformitÃ©
formelle (pas d'audit externe) ; MFA ; gestion des tiers ; rÃ©sistance Ã  une
attaque par dÃ©ni de service volumÃ©trique.

---

## 8. Enseignements

**Le risque dominant n'est pas l'attaque, c'est la panne silencieuse.** Sur les
11 dÃ©fauts corrigÃ©s pendant le projet, **aucun** n'Ã©tait une attaque rÃ©ussie.
Tous venaient d'une erreur d'exploitation ou d'une dÃ©pendance matÃ©rielle :

| DÃ©faut | Ce qu'il produisait |
|---|---|
| Image sans modÃ¨le ML | dÃ©tecteur en `degraded`, aucune dÃ©tection, aucune erreur |
| Seuil de calibration pÃ©rimÃ© | score d'attaque sous le seuil â†’ silence total |
| Webhook vers un hÃ´te inexistant | aucun incident n'arrivait au moteur |
| Verrou jamais rÃ©armÃ© | la **deuxiÃ¨me** attaque ne dÃ©clenchait plus rien |
| Playbook dans la requÃªte HTTP | timeout du dÃ©tecteur alors que la remÃ©diation marchait |
| `UNDER_ATTACK` Ã©crasÃ© en 3 ms | le rouge n'Ã©tait jamais visible |
| FenÃªtre non purgÃ©e | deux dÃ©mos consÃ©cutives se contaminaient |
| TÃ¢che Ansible dÃ©corative | playbook Â« rÃ©ussi Â» sans rien corriger |

La conclusion d'ingÃ©nierie : **un systÃ¨me qui Ã©choue bruyamment vaut mieux qu'un
systÃ¨me qui rÃ©ussit en silence.** C'est pourquoi chaque panne connue a produit
un contrÃ´le bloquant dans `e2e_demo.py`, une mÃ©trique dans `/metrics`, ou une
alerte Prometheus.

---

## 9. Pour aller plus loin

DÃ©tail complet des chantiers restants, avec critÃ¨res d'acceptation :
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

## Annexe A â€” Inventaire des endpoints

| Service | Port | Routes |
|---|---|---|
| `monero-crypto-core` | 8081 | `/healthz`, `/metrics`, `/crypto/{keys,stealth-address,mint-outputs,derive-one-time-key,check-output,audit-view,commit,commit-split,commitment-balance,ring-sign,ring-verify}` |
| `payment-api` | 8080 | `/healthz`, `/metrics`, `/outputs`, `/decoys`, `/wallet/public`, `/tx/submit`, `/tx/auto-probe/status`, `/pin/verify`, `/pin/stats`, `/audit/last`, `/audit/outputs/{id}`, `/admin/{quarantine-outputs,ring-size}` |
| `auth-service` | 8082 | `/healthz`, `/metrics`, `/auth/login` |
| `ml-detector` | 8083 | `/healthz`, `/metrics`, `/ingest`, `/score`, `/threshold` |
| `remediation-engine` | 8084 | `/healthz`, `/metrics`, `/incident`, `/state`, `/state/report`, `/events` (SSE), `/history` |
| `waf` | 8085 | `/healthz`, `/metrics`, `/admin/{redirect,reset,state}`, proxy `/*` |
| `shadow-api` | 8090 | `/healthz`, `/metrics`, `/decoys`, `/tx/submit`, `/admin/interceptions` |
| `backoffice` | 8086 | `/healthz`, `/metrics`, `/led/state`, `/auth/{bootstrap,identify/start,identify,password,me,logout}`, `/admin/{users,users/{id},users/{id}/face,users/{id}/unlock,audit,sessions,sessions/{id},stats}` |

## Annexe B â€” Reproduire les mesures

```bash
# ChaÃ®ne complÃ¨te
python tools/e2e_demo.py

# Validation par le vrai chemin de production
cd services/ml-detector && ../../.venv/bin/python validate_real.py --n 20

# SÃ©paration des features (AUC)
cd services/ml-detector && ../../.venv/bin/python analyze_separation.py

# Les 4 familles d'attaques
python tools/attack.py all

# Tous les tests
./tools/run_tests.sh
```

## Annexe C â€” VolumÃ©trie

| Ã‰lÃ©ment | Valeur |
|---|---|
| Services | 10 (dont 1 en Rust) |
| Conteneurs | 18 |
| Lignes de code (Python, Rust, JSX, C++, firmware) | ~13 300 |
| Routes HTTP | ~100 (dont 13 en Rust, 21 back-office, 14 boutique) |
| Tests automatisÃ©s | 141 |
| FenÃªtres d'entraÃ®nement | 1 129 |
| DurÃ©e de la chaÃ®ne P0 | 6,6 Ã  7,2 s (limite 90 s) |
