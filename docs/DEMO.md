# Démonstration — scénario de 3 minutes

> Runbook de la soutenance. À imprimer ou garder ouvert sur le PC Hôte.
> Grille visée : démonstration en direct (25 pts), Clip Chrono (15 pts),
> expertise technique (10 pts), gouvernance (10 pts).
>
> **Une seule chronologie** : le Clip Chrono est le storyboard du §2, 60 s
> continues. Les séquences du §2 ter (assistant RAG, Loki/Grafana, alerte SIEM)
> sont des démonstrations en direct, hors clip. Le PIN du terminal est généré :
> il se lit sur la fiche, jamais à l'écran (§0).

---

## 0. Avant d'entrer dans la salle (J-1 et J-0)

```powershell
cd C:\Users\seo\Documents\Work\cours\Shadow   # adapter si le dépôt a bougé
python tools/init_env.py          # seulement si .env n'existe pas encore
docker compose up -d
python tools/preflight.py         # code de sortie 0 = démontrable ; 1 = bloquant
.\tools\run_tests.ps1 -Fast       # Windows : toutes les suites, sans train.py --check
```

Sous macOS/Linux, adapter le `cd` et remplacer la dernière ligne par
`bash tools/run_tests.sh --fast`. Si PowerShell refuse le script
(`ExecutionPolicy`), lancer
`powershell -ExecutionPolicy Bypass -File .\tools\run_tests.ps1 -Fast`.

`tools/preflight.py` est **la** commande de contrôle : elle vérifie `.env`, la
transmission du PIN, la santé HTTP des services (dont la boutique, le RAG et
Loki), le catalogue et la propreté du dépôt. Un avertissement sur un service
non-P0 n'empêche pas de démontrer ; un échec bloquant, si. En cas de code 1,
lire la section « NON DÉMONTRABLE » et corriger avant d'ouvrir la salle.

La caméra est le seul point que le script ne peut pas tester : ouvrir une fois
http://localhost:5173, saisir l'identifiant et vérifier que la demande de caméra
apparaît. C'est un contrôle de 10 secondes, pas une réinstallation.

### Le PIN du jour

```
┌──────────────────────────────────────────────────────────────────────┐
│  Le PIN n'est pas une constante publique : il est tiré au hasard    │
│  par tools/init_env.py, puis transmis au conteneur par               │
│  docker-compose.yml (TERMINAL_PIN).                                  │
│                                                                      │
│  1. La veille, dans un terminal PRIVÉ (écran non projeté) :          │
│       Windows :                                                      │
│         Select-String -Path .env -Pattern '^TERMINAL_PIN='           │
│       macOS/Linux :                                                  │
│         grep '^TERMINAL_PIN=' .env                                   │
│  2. Le noter sur une fiche papier, puis effacer le terminal          │
│     (Clear-Host).                                                    │
│  3. Le jour J, le taper uniquement sur le pavé du dashboard.         │
│     Ne jamais l'afficher, ni le dicter, ni le coller dans un chat.   │
│  4. Vérifier avant la démo : python tools/preflight.py               │
│     → « secrets : PIN transmis à payment-api » doit être OK.         │
└──────────────────────────────────────────────────────────────────────┘
```

`preflight.py` ne montre jamais la valeur : il vérifie seulement qu'elle est
définie, qu'elle a le bon format et que `docker-compose.yml` la transmet bien à
`payment-api`. C'est le premier réflexe si un PIN est refusé (§6).

Si `preflight.py` signale « PIN public connu, à régénérer », le `.env` est
ancien. Régénérer **avant** de démarrer la pile :

```powershell
python tools/init_env.py --force
docker compose up -d --force-recreate
python tools/preflight.py
```

`--force` change aussi `SERVICE_TOKEN`, `BO_BOOTSTRAP_TOKEN` et
`LED_DEVICE_TOKEN` : recréer tous les conteneurs, et reporter le nouveau jeton
du bandeau dans `hardware/led_bandeau_esp8266/src/secrets.h` avant de flasher.
Ne jamais lancer `--force` pendant que la pile tourne.

**Réinitialiser la déception** entre deux répétitions :
`python tools/attack.py reset`

**Réinitialiser le back-office** (comptes bloqués, sessions ouvertes) :
débloquer depuis l'écran « Habilitations », ou `docker compose restart backoffice`
(les comptes et les visages sont conservés dans le volume `bo-data`).

---

## 1. Répartition des rôles

| Rôle | Qui | Où | Fait quoi |
|---|---|---|---|
| Présentateur | 1 personne | PC Hôte, debout | parle, montre le dashboard, tape le paiement final |
| Attaquant | 1 personne | **PC Attaquant** (poste distinct) | lance `tools/attack.py poison` |
| Chronométreur | 1 personne | salle | chronomètre, annonce les temps |
| Régie | 1 personne | PC Hôte | ne touche à rien sauf incident |

Règle absolue : **aucune intervention humaine** entre l'attaque et la reprise.
Si quelqu'un touche au clavier pendant la remédiation, l'axe 1 est perdu.

---

## 2. Le scénario, seconde par seconde — 60 s continues

C'est le **seul** storyboard. Il contient les deux facteurs, la boutique, la
chaîne P0 et la déception, dans cet ordre. Une seule prise, aucune coupe.

| Temps | Action | Ce qu'on voit | Ce qu'on dit |
|---|---|---|---|
| 0–5 s | Le présentateur tape son **identifiant** (compte administrateur) | la **caméra USB du PC hôte** s'allume ; le champ mot de passe reste fermé | « Le back-office n'accepte plus un simple mot de passe : l'identifiant déclenche la caméra. » |
| 5–11 s | Il lance le **scan du visage** | **LED verte clignotante** (bandeau ESP8266 + miroir à l'écran) | « Le visage est comparé au descripteur enrôlé pour ce compte. La carte passe au vert clignotant : le mot de passe est attendu. » |
| 11–18 s | Il tape le **mot de passe** | **LED verte fixe 30 s**, dashboard ouvert | « Deux facteurs : identifiant, visage, mot de passe. Un visage inconnu vide l'identifiant ; deux échecs bloquent cinq minutes. » |
| 18–23 s | Il ouvre la **vitrine** (page publique) | catalogue de **6 articles**, images, prix et stock affichés | « Le SI de paiement est un site vitrine branché sur PostgreSQL : chaque article est un enregistrement, pas une page figée. » |
| 23–28 s | « Ajouter au panier » sur **deux articles** | panier, total recalculé, stock vérifié | « Le panier est en base, le stock est réservé à l'ajout. » |
| 28–33 s | Il clique **Payer** | commande `paid`, **key image** affichée | « Le paiement n'est pas simulé : le noyau émet un output du montant exact et le dépense par une signature d'anneau bLSAG. » |
| 33–38 s | Il recharge la page et ouvre l'**historique** | la commande, son paiement et ses événements sont là | « La commande et sa preuve sont en base : elles survivent à un redémarrage. » |
| 38–42 s | **L'attaquant lance** `python tools/attack.py poison` depuis le PC Attaquant | score ML qui grimpe, badge **SOUS ATTAQUE** | « Attaque depuis un poste distinct : l'attaquant empoisonne les leurres de l'anneau. » |
| 42–46 s | Rien | seuil franchi, badge **REMEDIATION**, console Ansible | « Aucune intervention humaine : le détecteur a franchi son seuil appris et déclenche le playbook. » |
| 46–52 s | Rien | journal Ansible qui défile : déception, quarantaine, durcissement de l'anneau de 11 à 25 | « Déception active, quarantaine des outputs compromis, anneau durci. » |
| 52–57 s | **Le présentateur retape un paiement** avec le **PIN du jour** (fiche §0) | transaction acceptée, nouvelle ligne ; badge **RETABLI** | « Reprise en 7 secondes : un client peut de nouveau payer. C'est ça, la résilience. » |
| 57–60 s | L'attaquant relance `poison` | requêtes **déroutées** vers le honeypot | « L'attaquant, lui, tape maintenant dans le vide : il est contenu. » |

**Deux précisions qui évitent une erreur en salle :**

1. Le paiement de la boutique (28–33 s) est un **paiement en ligne** : il ne
   demande pas le PIN. Le PIN est l'authentification du **terminal physique**,
   utilisé à 52 s pour la transaction de preuve. Ne pas confondre les deux.
2. **Le geste de 52 s est le cœur de la démo.** C'est la preuve qu'un *client*
   paie de nouveau. Ne jamais le présenter comme une action d'administration.

**Le scan du visage (5–11 s) est la réponse à la consigne additionnelle.** Ne
pas couper la caméra : c'est la preuve visuelle que l'authentification est
réelle, et le bandeau LED la rend lisible à distance sans regarder l'écran.

Le chronométreur annonce à voix basse **18 s** (fin de l'auth), **38 s** (début
de l'attaque) et **57 s** (déception). Entre 38 et 52 s, personne ne touche au
clavier.

**Le storyboard n'est pas le chronomètre système.** La chaîne mesurée
(attaque → détection → playbook → reprise) prend **6,6 à 7,2 s** ; les 19 s
allouées ici laissent le temps de lire le journal Ansible et de commenter. Si
le playbook finit avant 52 s, on garde le plan sur la console et on attend le
top du chronométreur : ne pas enchaîner sur la déception pendant que le badge
est encore orange.

### 2.1 Variante 90 s (si le jury laisse plus de temps)

Même ordre, prise continue de 90 s. On ajoute le back-office boutique et le
quadrant RAG ; on ne réordonne rien.

| Temps | Séquence | Ce qu'on ajoute par rapport au 60 s |
|---|---|---|
| 0–18 s | Authentification | identique : identifiant → visage → mot de passe |
| 18–30 s | Vitrine | catalogue, images, stock ; ouvrir un article et montrer le prix en base |
| 30–38 s | Panier | deux articles, total recalculé |
| 38–48 s | Paiement réel | clic **Payer**, key image, commande `paid` |
| 48–54 s | Historique + back-office | historique client, puis « Habilitations → Boutique » : articles et commandes |
| 54–58 s | Attaque | `python tools/attack.py poison` depuis le PC Attaquant |
| 58–68 s | ML + Ansible | score, seuil, badge **REMEDIATION**, journal du playbook |
| 68–78 s | Reprise | badge **RETABLI**, nouveau paiement avec le PIN du jour |
| 78–82 s | Déception | `poison` relancé, requêtes déroutées |
| 82–90 s | Assistant RAG | une question, les sources citées, le moteur (`extractive` ou `ollama:…`) |

Les séquences Loki et alerte SIEM du §2 ter restent **hors clip** : elles
prennent 20 à 90 s chacune et se montrent avant ou après, pas dans la prise.

### 2.2 Si le jury ne laisse que 60 s

Le storyboard ci-dessus tient déjà en 60 s. Si l'authentification a été faite
**avant** le clip (connexion déjà ouverte, caméra déjà montrée), appliquer la
variante « sans auth » :

| Temps | Séquence |
|---|---|
| 0–20 s | Boutique : catalogue → panier → paiement réel → historique |
| 20–39 s | Chaîne P0 : attaque → ML → Ansible → reprise |
| 39–42 s | Déception |

Il reste 18 s de marge : soit on s'arrête à 42 s (la contrainte est « au plus
60 s », pas « exactement 60 »), soit on garde un plan large sur le dashboard.
Si l'auth doit rester dans le clip, compresser **la boutique** à 12 s
(catalogue 3 s, panier 3 s, paiement 4 s, historique 2 s), pas la chaîne P0.

**Ce qu'on ne coupe jamais** : le paiement de reprise (52 s) et la déception
(57 s). Ce sont les deux preuves. On coupe d'abord le survol du catalogue, puis
la lecture du journal Ansible, jamais la reprise.

---

## 2 bis. La boutique — ce qu'il faut savoir

La boutique n'a pas de chronologie séparée : elle est dans le storyboard
(18–38 s). Voici ce qu'on montre si le jury pose une question après le clip.

| Action | Ce qu'on voit | Ce qu'on dit |
|---|---|---|
| « Habilitations » → « Boutique » | tableau des articles et des commandes | « Le back gère le catalogue et voit la preuve de chaque paiement. » |
| Modifier le stock, retirer un article | la vitrine se met à jour | « Une seule source de vérité : la base. » |
| Ouvrir une commande | lignes, montant, key image, événements | « Le prix est recopié au moment de l'achat : l'historique reste juste même si le tarif change. » |

**Ce qu'il ne faut pas dire** : « c'est une démo ». Le stock est réellement
décrémenté, la commande est réellement payée, et un paiement refusé produit une
commande `failed` avec son motif.

**Q&A — « et si le paiement échoue ? »**
Le stock réservé est restitué dans la même seconde, la commande passe en
`failed`, le panier est conservé. C'est vérifié par un test
(`test_le_paiement_refuse_restitue_le_stock`) : pas de stock fantôme. Si le
processus meurt avant l'appel de paiement, la commande reste `pending` : le
balayage la passe en `failed` et restitue le stock après le TTL (120 s par
défaut, balayage toutes les 30 s).

---

## 2 ter. Les trois séquences exigées le 20/10

Ces trois séquences sont des **exigences du jury du 20/10**. Elles se montrent
en direct, hors clip, dans l'ordre que le temps permet. Elles ne remplacent pas
le storyboard du §2.

### A. Assistant RAG — poser une question et montrer les sources (20 s)

1. Dans le dashboard, descendre au quadrant **« Assistant RAG — dossier
   technique »**.
2. Cliquer la question d'exemple **sur la protection du stock** (la troisième
   du quadrant) ; elle interroge la base, pas une page figée.
3. Lire la réponse, puis montrer **les sources citées** (fichier, section, score
   BM25) et la ligne **`moteur …`**.

```
moteur extractive · … ms
docs/SECURITE.md §… · score …
docs/COMPTE-RENDU.md §… · score …
```

Ce qu'on dit : « L'index de l'image de démonstration contient **14 documents et
597 passages** du dossier. La réponse cite ses sources ; le moteur est
`extractive` si Ollama n'est pas joignable, `ollama:llama3.1:8b` sinon. Il ne
devine pas : sans passage, il dit qu'il ne sait pas. »

Vérification en une commande :

```powershell
curl.exe -s http://localhost:8091/healthz
```

Attendu sur l'image actuelle : `"documents": 14`, `"chunks": 597`, et
`generation.ollama` qui vaut `reachable` ou `unreachable`. Après un rebuild,
`/healthz` fait foi. Si le quadrant est vide :
`docker compose up -d rag-assistant`.

### B. Loki / Grafana Explore — lire les logs (20 s)

**À faire à J-1, pas devant le jury** : la source Loki n'est pas provisionnée
par défaut dans Grafana (seul Prometheus l'est). L'ajouter une fois :
**Connections → Data sources → Add data source → Loki**, URL
`http://loki:3100`, puis **Save & test**. Sans cela, Explore n'a que
Prometheus et la démonstration tombe à plat.

1. Ouvrir http://localhost:3001 (identifiant `admin`, mot de passe
   `GRAFANA_PASSWORD` dans `.env`, lu en terminal privé) → **Explore** →
   source **Loki**.
2. Requête `{service="payment-api"}` sur les **15 dernières minutes**, puis
   filtrer le texte `ERROR` — requête complète :
   `{service="payment-api"} |= "ERROR"`.
3. Montrer une ligne et son contexte. Pour un refus de paiement, la ligne est
   écrite par **shop-api** (et non par payment-api) :

```
{service="shop-api"} |= "paiement refusé"
```

Ce qu'on dit : « Prometheus dit *combien* ; Loki dit *pourquoi*. La même ligne
`paiement refusé` alimente la règle `PaiementRefuse`, qui pousse une alerte à
Alertmanager, qui appelle le moteur de remédiation. »

Si Explore est vide : vérifier la plage de temps (15 min), puis
`docker compose ps promtail loki` et `docker compose logs --tail=50 promtail`.

### C. Alerte SIEM réelle — arrêter le noyau, acheter, remédier (60–90 s)

C'est la séquence « l'IA agit sur une alerte », avec une panne **réelle** et non
un `curl` de confort. Elle se fait hors clip, après le storyboard.

1. **Provoquer la panne** :

```powershell
docker compose stop monero-crypto-core
```

2. **Acheter dans la vitrine** : ajouter un article, cliquer **Payer**. Le
   paiement échoue, la commande passe en `failed`, le stock est restitué, et
   `shop-api` journalise `paiement refusé`.

3. **Déclencher l'alerte**. Alertmanager le fera tout seul après l'évaluation
   de la règle Loki (30 s). Pour être déterministe en démonstration, injecter
   exactement la même alerte :

```powershell
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
```

4. **Montrer la remédiation** : le dashboard passe en **SOUS ATTAQUE**, puis
   **REMEDIATION** (console Ansible). La même alerte apparaît dans
   `detail.alerts` ; trois à cinq alertes en cascade dans les 10 s sont
   corrélées au **même** incident, pas cinq incidents distincts.

   Le playbook **ne redémarre pas** `monero-crypto-core` : le moteur n'a pas le
   socket Docker (accès root sur l'hôte refusé). Il contient l'incident
   (déception, quarantaine, durcissement), mais l'étape 4 — la transaction de
   preuve — échoue tant que le noyau est arrêté. Le badge finit donc en
   **REMEDIATION_FAILED**, pas en RETABLI. **C'est le comportement correct** :
   la preuve de reprise n'est pas décorative. On le dit à voix haute.

5. **Réparer et re-payer** :

```powershell
docker compose start monero-crypto-core
docker compose ps monero-crypto-core payment-api   # attendre "healthy"
python tools/preflight.py
```

Attendre que `payment-api` soit `healthy` et que
`curl localhost:8080/tx/auto-probe/status` repasse à `"ok": true` (sonde toutes
les 4 s), puis refaire un achat : la commande passe en `paid` et la première
transaction réussie repasse le badge à **NORMAL**. On n'aura pas le **RETABLI**
du clip : ici, la reprise prouvée par le playbook a échoué, et c'est assumé.

Ce qu'on dit : « L'alerte n'est pas seulement affichée : elle ouvre un incident
et lance le même playbook que le détecteur ML. Une alerte de confort est
journalisée sans agir ; une alerte actionnable agit. »

**À ne pas oublier** : `monero-crypto-core` est un service P0. Tant qu'il est
arrêté, `preflight.py` est rouge. Le redémarrer avant la séquence suivante.

---

## 3. Les attaques disponibles

```powershell
python tools/attack.py list      # rappel des commandes
python tools/attack.py poison    # attaque principale — détectée à 100 %
python tools/attack.py replay    # rejeu de key image — bloqué en 409
python tools/attack.py burst     # rafale automatisée — cadence machine
python tools/attack.py malformed # entrées invalides — 400, jamais de panic
python tools/attack.py all       # les quatre, pour une répétition
python tools/attack.py shop      # 6 attaques sur la boutique
```

`attack.py shop` répond à « les CYBER doivent essayer de faire tomber le SI » :
six familles d'attaques sur la vitrine et son panier, en une seconde, avec un
code de sortie. Chaque attaque vérifie le code HTTP **et** l'absence d'effet de
bord (aucune commande créée, stock jamais négatif, service vivant).

```
quantités invalides      5/5 refusées (422)
panier forgé             4/4 refusés
injection SQL par SKU    3/3 refusées, catalogue intact
historique d'autrui      3/3 refusés, 0 commande retournée
course sur le stock      aucune survente
falsification de prix    montant facturé = prix en base
```

Détail des failles fermées et des limites assumées : `docs/SECURITE.md`.

| Attaque | Ce qu'elle exploite | Ce qui la détecte |
|---|---|---|
| `poison` | Tirage **uniforme** des leurres au lieu de la gamma biaisée récents | `decoy_median_age_days` (AUC 1,00) |
| `replay` | Même output, donc même key image | Anti-double-dépense (409) + télémétrie |
| `burst` | Cadence machine (50 ms) au lieu d'humaine | `inter_arrival_median` |
| `malformed` | Anneau trop court, clés invalides | 400 propre, pas de panic |

---

## 4. Ce qu'il faut montrer à l'écran (Clip Chrono)

Le clip nominal est **celui du §2** : une seule prise, 60 s, sans coupe. La
variante 90 s (§2.1) ne s'applique que si le jury l'accorde. Cadrage conseillé :

```
┌──────────────────────────────────────────────────────────┐
│  [terminal physique]        [dashboard plein écran]      │
│   anneau de LEDs            badge d'état + score ML      │
│                             console Ansible              │
│                             audit view-only              │
└──────────────────────────────────────────────────────────┘
```

Trois éléments doivent rester lisibles **à distance** :

1. le **badge d'état** (NORMAL → SOUS ATTAQUE → REMEDIATION → RETABLI) ;
2. le **score ML et son seuil** (deux nombres, pas une courbe) ;
3. l'**anneau de LEDs** (rouge = attaque, orange = remédiation, vert = reprise).

Le quadrant « Audit view-only » est un bonus : il montre qu'un auditeur lit le
montant **sans pouvoir dépenser**. C'est l'argument DORA art. 19. Le quadrant
RAG est plus bas dans la même page : le montrer après le clip, pas en scrollant
pendant les 60 s.

---

## 5. Q&A — les sept questions qui viendront

**« Une photo de l'utilisateur suffit-elle à passer ? »**
Oui, aujourd'hui : il n'y a pas de détection de vivacité, et c'est écrit dans
`/healthz` (`liveness: not_implemented`). Le visage est un facteur d'identité,
pas un anti-spoofing ; le mot de passe reste le second facteur, et il n'est
demandé qu'après la reconnaissance. Ce qui est réellement couvert : un
identifiant volé ne suffit plus, un mot de passe volé ne suffit plus, et une
photo seule ne suffit pas non plus (il faut aussi le mot de passe).

**« Pourquoi la caméra est-elle ouverte par le navigateur et pas par un
conteneur ? »**
Parce que la consigne impose la caméra sur le **PC hôte**, et que Docker
Desktop/WSL2 n'a pas de passthrough webcam fiable. Le navigateur du PC hôte est
le seul composant qui a un accès garanti au matériel. Le serveur reçoit une
image JPEG ponctuelle, en calcule le descripteur, et n'en conserve **aucune**.

**« Votre détection, c'est un seuil fixe déguisé ? »**
Non. Le seuil est appris sur une capture de trafic réel (**1 129 fenêtres**),
prend le max de deux estimateurs, et est stocké dans `threshold.json` avec les
métriques. `grep -r "threshold.*=.*[0-9]" services/ml-detector/*.py` ne renvoie
aucune constante d'exploitation. Le seuil runtime mesuré est **refusé** s'il
provient d'un autre modèle (`runtime_threshold_ignored`).

**« Une seule feature porte votre détection, ce n'est pas fragile ? »**
C'est assumé et mesuré. `decoy_median_age_days` a une AUC de 1,000 sur
l'attaque principale, et elle est physiquement justifiée : Monero tire selon une
gamma biaisée récents (**3,850 j ± 0,167**), un attaquant pioche uniformément
(**25,731 j ± 1,859**). Les cinq autres features couvrent les autres familles
d'attaques (rejeu, rafale, anneau malformé). Un modèle à une seule feature ne
détecterait qu'une attaque.

**« Votre crypto, c'est du vrai Monero ? »**
C'est un modèle Monero **simplifié mais réel** : Ristretto255, adresses
one-time par ECDH, key image `I = x·H_p(P)` avec un vrai hash-vers-groupe (donc
pas de key image forgeable), engagements de Pedersen, signature d'anneau liée
**bLSAG**, et déchiffrement par View Key séparée. Ce n'est pas CLSAG (la
signature est ~2× plus grosse) et il n'y a pas de Bulletproofs : un montant
négatif modulo l passerait la vérification d'équilibre. Les deux écarts sont
dans `/healthz` et dans le dossier de gouvernance.

**« Et si l'objet physique tombe ? »**
Rien. Le pont est en lecture seule, aucun abonné SSE n'est obligatoire, et
aucun chemin ne va du terminal vers le moteur de remédiation. C'est testé
(`test_hardware_bridge.py` vérifie l'absence de POST).

**« Comment savez-vous que ça marche sur un autre poste ? »**
On ne le sait pas encore, et c'est écrit. Le modèle doit être recalibré sur le
PC hôte (7 minutes, `capture_baseline.py` + `train.py`). Un seuil calibré
ailleurs produit des faux positifs — c'est arrivé trois fois pendant le projet.

---

## 6. Si ça casse pendant la démo

**Premier réflexe, toujours** : `python tools/preflight.py`. Il distingue un
service P0 arrêté (bloquant) d'un service de confort absent (avertissement), et
il vérifie le PIN et les secrets sans les afficher.

| Symptôme | Geste immédiat | Ce qu'on dit |
|---|---|---|
| La caméra ne s'ouvre pas | fermer Teams/Zoom, recharger la page ; vérifier que l'URL est `localhost` | « une autre application tient la caméra, je la libère » |
| « visage non reconnu » sur le bon utilisateur | se placer face à la lumière, visage entier dans le cadre, relancer le scan | « la lumière du vidéoprojecteur me met à contre-jour » |
| Compte bloqué après deux essais | écran « Habilitations » → débloquer (ou attendre 5 min) | « le blocage est une fonctionnalité, pas une panne » |
| LED du bandeau éteinte | l'authentification fonctionne quand même ; vérifier l'alim et le WiFi de l'ESP | « l'objet est un miroir, la chaîne ne dépend pas de lui » |
| **PIN refusé** | lire `TERMINAL_PIN` dans `.env` (terminal privé), comparer avec la fiche, puis `python tools/preflight.py` ; si le `.env` a changé : `docker compose up -d --force-recreate payment-api` | « le PIN est un secret de déploiement, pas une constante ; je vérifie la valeur transmise » |
| **PIN refusé et `preflight` voit `pin_source: generated`** | le conteneur a tiré un PIN au hasard faute de variable : `docker compose logs payment-api`, chercher `PIN aléatoire` (affiché une seule fois), ou remettre `TERMINAL_PIN` dans `.env` puis recréer | « la variable n'arrivait pas au conteneur ; je la rétablis » |
| **Boutique 503 / catalogue vide** | PostgreSQL absent : `docker compose ps db-postgres`, puis `docker compose up -d db-postgres shop-api` ; `curl localhost:8087/healthz` doit dire `database: postgresql` | « la vitrine ne sert pas sans base : je redémarre PostgreSQL puis la boutique » |
| **Commande bloquée en `pending`** | ne pas relancer le paiement sur la même commande ; attendre le balayage (TTL 120 s, toutes les 30 s) : elle passe en `failed` et le stock est restitué | « une réservation expirée est réparée automatiquement, le stock revient » |
| **`/history` injoignable** | la console Ansible affiche « indisponible » ; `curl localhost:8084/history` ; ne pas redémarrer le moteur pendant le playbook | « la console lit l'historique, le playbook continue ; je vérifie à la fin » |
| Badge bloqué sur NORMAL, score bas | L'attaquant relance `poison` | « L'attaque n'a pas porté, je la relance. » |
| Score monte mais rien ne se passe | Vérifier `curl localhost:8083/healthz` → `last_webhook_error` | « Le webhook est cassé, je le signale. » |
| `REMEDIATION_FAILED` | `curl localhost:8084/history` → `ansible_rc` | « Le playbook a échoué, voici la trace. » |
| Dashboard figé | `docker compose restart front-dashboard` | « Le dashboard est un miroir, la chaîne continue. » |
| **Assistant RAG muet** | `docker compose up -d rag-assistant` ; `curl localhost:8091/healthz` | « l'index est dans l'image ; sans lui, l'assistant le dit » |
| **Grafana Explore vide** | vérifier que la source **Loki** existe (§2 ter B), plage de temps 15 min, `docker compose ps promtail loki` | « les logs sont poussés par promtail ; je vérifie la source et la plage » |
| Plus rien ne répond | `docker compose up -d` puis `python tools/preflight.py` | « Je relance la pile et je vérifie. » |

**Éviter** de redémarrer `payment-api` en pleine démonstration : cela coupe le
service quelques secondes. Le registre d'outputs, lui, est persistant
(PostgreSQL) : il est rechargé au démarrage.

---

## 7. Après la démo

```powershell
python tools/preflight.py       # doit repasser en code 0
python tools/e2e_demo.py        # recette complète de la chaîne
python tools/attack.py reset    # désarmer la déception
curl.exe -s http://localhost:8084/history | python -m json.tool   # trace pour le dossier
```

Récupérer les chiffres du jour pour le dossier technique :

```powershell
python tools/e2e_demo.py | Tee-Object docs/mesures-soutenance.txt
curl.exe -s http://localhost:8083/threshold | python -m json.tool
```
