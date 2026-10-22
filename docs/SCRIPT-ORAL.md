# Script oral — ce qu'on dit, ce qu'on clique

> À imprimer. Une page par séquence. Le texte entre guillemets est **à dire
> tel quel** ; le reste est un geste. Les temps sont ceux du chronomètre.
>
> Deux supports existent désormais dans `docs/` :
> `PRESENTATION-SOUTENANCE.pptx` (16 diapositives, **le texte est dans les notes
> du présentateur**) et `DOSSIER-INGENIERIE-GOUVERNANCE.pdf` (11 pages +
> annexes).

---

## 0. La veille — 10 minutes, dans cet ordre

```bash
cd ~/Documents/dev/Shadow
colima status || colima start --cpu 4 --memory 8
docker compose up -d
python tools/preflight.py          # doit dire DÉMONTRABLE
python tools/warmup_rag.py         # Ollama chaud : sinon 10 s au 1er appel
python tools/attack.py reset
python tools/restock.py
python tools/backoffice_check.py   # 0 échec (après enrôlement)
```

Puis, **dans le navigateur du PC hôte** :

1. `http://localhost:5173` → « Espace back-office » → identifiant → **visage** →
   mot de passe. Se déconnecter ensuite : le jour J, on veut montrer ce parcours.
2. Vérifier que la barre du bas affiche **5 boutons** :
   `1 · Vitrine`, `2 · Supervision`, `3 · Habilitations`, `4 · Admin boutique`,
   `5 · Assistant RAG`. C'est la nouvelle navigation : plus besoin de chercher
   « Habilitations → Boutique », ni de faire défiler la supervision pour
   atteindre l'assistant.
3. Ouvrir un onglet `http://localhost:3001` (Grafana, admin/admin) sur
   **Explore → Loki**, requête `{service="shop-api"}`. Laisser l'onglet prêt.
4. Ouvrir un onglet `http://localhost:9093` (Alertmanager) — optionnel.

Sur le **PC Attaquant**, vérifier une seule fois :

```bash
curl -s http://<IP-PC-HOTE>:8085/healthz     # {"status":"ok","service":"waf"...}
```

`caffeinate -dimsu &` sur le Mac, Ne pas déranger activé, alimentation branchée.

---

## 1. Le pitch — 5 minutes, diapositive par diapositive

> Le support contient 16 diapositives. En 5 minutes, on n'en montre que 9 :
> 1, 2, 3, 4, 7, 9, 10, 11, 12. Les autres restent pour les questions.

### Diapo 1 — Couverture (20 s)

> « Bonjour. Nous sommes le groupe [n]. Notre sujet s'appelle RESILIENCE-ALPHA :
> un système de paiement fictif qui doit encaisser une attaque et se rétablir
> seul. Une phrase pour planter le décor : tout système finit par tomber ; la
> vraie question, c'est si le vôtre saura se relever seul. C'est exactement ce
> qu'on va vous montrer, en direct, depuis deux machines distinctes. »

**Ne pas** lire les quatre chiffres : ils sont là pour l'œil.

### Diapo 2 — Le problème (30 s)

> « Le point de départ est banal : la plateforme tombe, et on la relance à la
> main. C'est lent, ce n'est pas reproductible, et surtout ça ne prouve rien.
> Le sujet interdit deux raccourcis : une attaque pour de faux, et une détection
> par seuils fixes. Il impose une attaque réelle, un modèle entraîné, et une
> preuve : une nouvelle transaction qui réussit. »

### Diapo 3 — La chaîne en quatre temps (30 s)

> « Quatre temps. Un poste distinct attaque. Le détecteur franchit un seuil qu'il
> a appris. Le moteur lance un vrai playbook Ansible. Et la preuve, ce n'est pas
> un voyant vert : c'est un client qui paie de nouveau. Personne ne touche au
> clavier entre l'attaque et la reprise. »

### Diapo 4 — Architecture (45 s)

> « Je ne vais pas décrire dix-neuf conteneurs. Regardez les flèches. L'attaquant
> entre par le WAF, comme un client. Le paiement appelle le noyau Rust à chaque
> transaction. La télémétrie part vers le détecteur, qui appelle le moteur, qui
> exécute Ansible. Deux choix à retenir : le WAF ne déclenche rien, il obéit au
> playbook ; et le ML est hors du chemin critique — si on l'arrête, le paiement
> continue. »

### Diapo 7 — La détection (45 s)

> « C'est le point que vous allez tester. Le seuil n'est pas dans le code : il
> est appris sur une capture du trafic de ce poste, 1 287 observations. Pourquoi
> une seule feature porte l'attaque ? Parce qu'un wallet tire ses leurres selon
> une gamma biaisée vers les récents — 3,6 jours — alors qu'un attaquant qui
> empoisonne pioche uniformément — 26,9 jours. Aucun chevauchement. Et on exige
> trois fenêtres consécutives : un pic isolé ne déclenche pas. »

### Diapo 9 — Les preuves (30 s)

> « Ces chiffres sont ceux de ce matin, pas d'il y a trois semaines : trois
> exécutions de suite, 7,2, 7,4 et 6,7 secondes, pour une limite de 90. La marge
> sert à lire le journal Ansible, pas à attendre. »

### Diapo 10 — La boutique (30 s)

> « Les règles du 20 octobre demandaient un site vitrine connecté à une base.
> Six articles sont en PostgreSQL, le panier est en base, le stock est réservé
> dans une transaction, et une commande n'est payée qu'après une vraie signature
> d'anneau. C'est un achat invité : le ticket porte un nom et un e-mail,
> l'historique est protégé par un jeton de capacité. »

### Diapo 11 — L'IA (30 s)

> « Deux briques. Le RAG répond sur le dossier et cite ses sources ; sans Ollama
> il passe en extractif et le dit — il n'invente pas. Et l'ingestion SIEM : une
> alerte actionnable ouvre un incident et lance le même playbook que le ML. Une
> alerte de confort est journalisée sans agir. »

### Diapo 12 — Sécurité (30 s)

> « On ne dit pas invulnérable. On dit : douze failles fermées, quatre bugs
> critiques, et un test nommé pour chaque correction. La plus parlante : deux
> curl simultanés réussissaient une double dépense — la propriété centrale du
> projet était contournable. »

### Transition vers la démo

> « Assez de diapositives. On vous montre. »

---

## 2. La démo live — 3 minutes, clic par clic

> **Règle absolue** : personne ne touche au clavier entre 38 s et 52 s.
> Le chronométreur annonce à voix basse **18**, **38**, **57**.

### 0–18 s · Authentification à deux facteurs

**Sur le PC hôte, navigateur déjà ouvert sur `http://localhost:5173`.**

| Geste | Ce qu'on voit | Ce qu'on dit |
|---|---|---|
| Cliquer **Espace back-office** | page de connexion | « Le back-office n'accepte plus un simple mot de passe. » |
| Taper l'identifiant (ex. `admin`) puis **Suivant** | la caméra s'allume, le champ mot de passe reste fermé | « L'identifiant déclenche la caméra. » |
| Cliquer **Scanner** | LED verte clignotante à l'écran (miroir du bandeau) | « Le visage est comparé au descripteur enrôlé. Vert clignotant : le mot de passe est attendu. » |
| Taper le mot de passe puis **Valider** | vert fixe 30 s, dashboard ouvert | « Deux facteurs : identifiant, visage, mot de passe. Un visage inconnu vide l'identifiant ; deux échecs bloquent cinq minutes. » |

> Le mot de passe est sur la fiche papier. **Ne jamais le dicter.**

### 18–38 s · Boutique

| Geste | Ce qu'on voit | Ce qu'on dit |
|---|---|---|
| Barre du bas → **1 · Vitrine** | 6 articles avec image, prix, stock | « Le SI est un site vitrine branché sur PostgreSQL : chaque article est un enregistrement, pas une page figée. » |
| **Ajouter au panier** sur 2 articles | panier, total recalculé | « Le panier est en base, le stock est réservé à l'ajout. » |
| Remplir **Nom** et **E-mail** dans le panier | le bouton passe de « Renseigner nom + e-mail » à « Payer » | « Pas de compte : le ticket porte l'identité de l'acheteur, l'historique sera protégé par un jeton. » |
| Cliquer **Payer** | **Ticket CMD-…**, badge `paid`, key image, anneau | « Le paiement n'est pas simulé : le noyau émet un output du montant exact et le dépense par une signature d'anneau bLSAG. » |
| Recharger la page (F5) | le ticket est toujours là | « La commande et sa preuve sont en base : elles survivent au redémarrage. » |

### 38–42 s · L'attaque

**L'attaquant, sur le PC Attaquant, tape une seule commande** (déjà préparée
dans le terminal, il ne fait que la valider) :

```bash
SHADOW_HOST=<IP-PC-HOTE> python tools/attack.py poison
```

| Ce qu'on voit | Ce qu'on dit |
|---|---|
| sur le PC Attaquant : `12 acceptées · 0 rejetées` | « Attaque depuis un poste distinct : l'attaquant empoisonne les leurres de l'anneau. » |
| sur le PC hôte : score ML qui grimpe, badge **SOUS ATTAQUE** | « Le score monte : c'est la feature d'âge médian des leurres. » |

### 42–52 s · Remédiation — personne ne touche à rien

| Ce qu'on voit | Ce qu'on dit |
|---|---|
| badge **REMEDIATION** (orange) | « Aucune intervention : le seuil appris est franchi, le playbook démarre. » |
| journal Ansible qui défile | « Déception active, quarantaine des outputs compromis, anneau durci de 11 à 25. » |
| badge **RETABLI** (vert) | « Reprise en 7 secondes. » |

### 52–57 s · La preuve

| Geste | Ce qu'on voit | Ce qu'on dit |
|---|---|---|
| Barre du bas → **2 · Supervision**, puis taper le **PIN du jour** sur le pavé | `TX DE PREUVE : OK`, nouvelle ligne | « Un client peut de nouveau payer. C'est ça, la résilience. » |

> Le PIN est sur la fiche. Ne pas le montrer à l'écran avant de le taper.

### 57–60 s · La déception

**L'attaquant relance la même commande** (flèche ↑ puis Entrée) :

| Ce qu'on voit | Ce qu'on dit |
|---|---|
| `déroutées vers le honeypot` sur le PC Attaquant | « L'attaquant, lui, tape maintenant dans le vide : il est contenu. » |

### Après le chrono (hors clip) — 1 minute, si le jury le permet

1. **5 · Assistant RAG** : cliquer la question
   d'exemple « comment le stock est-il réservé ? ». Montrer la réponse, les
   sources et la ligne `moteur ollama:llama3.1:8b`.
2. **4 · Admin boutique** : montrer la commande, le client, la key image.
3. Onglet **Grafana → Explore → Loki** : `{service="shop-api"} |= "paiement"`.
4. **3 · Habilitations** : montrer les rôles et le journal d'audit.

---

## 3. Comment le PC Attaquant attaque — la mécanique exacte

### Ce qui est installé là-bas

Un interpréteur Python et **une** dépendance :

```bash
python3 -m venv .venv-attaque
.venv-attaque/bin/pip install httpx
```

Puis trois fichiers copiés depuis le dépôt :

```
tools/attack.py
tools/attack_shop.py
tools/service_token.py
```

**Aucun secret** : le jeton de service reste sur le PC hôte. C'est volontaire —
un attaquant n'a pas les clés de l'administrateur.

### Ce que fait la commande, concrètement

`attack.py poison` n'exécute aucun code sur le PC hôte. Il fait des requêtes
HTTP normales vers le WAF :

1. `GET http://<hôte>:8085/outputs?unspent=true` — il lit le pool public ;
2. il choisit **uniformément** 10 leurres (au lieu de la gamma biaisée récents) ;
3. il `POST /tx/submit` douze fois avec un vrai output et ces leurres ;
4. il affiche `12 acceptées · 0 rejetées`.

Ce que le PC hôte observe : la télémétrie de `payment-api` alimente
`ml-detector`, l'âge médian des leurres passe de ~3,6 j à ~27 j, le score
dépasse 6,715 sur trois fenêtres, et le moteur ouvre l'incident.

**Le PC Attaquant ne peut pas** : désarmer la déception (`reset` exige le
jeton), redémarrer un service, lire `.env`. Il ne fait qu'attaquer.

### Ce que le jury voit à l'écran

Idéalement, les deux écrans côte à côte : le terminal de l'attaquant (deux
lignes, aucun secret) et le dashboard (badge + score + journal). Si un seul
vidéoprojecteur : on projette le PC hôte et l'attaquant **annonce** à voix haute
ce qu'il tape.

### Réseau

Les deux postes doivent être sur le même réseau, et le PC hôte doit être
joignable sur **8085**, **8087** et **8080**. Vérifié sur ce poste :
`curl http://10.251.4.134:8085/healthz` répond depuis le réseau local.
Détail complet : [`PC-ATTAQUANT.md`](PC-ATTAQUANT.md).

---

## 4. « Payer sans compte, ça n'a pas de sens » — la réponse

C'est une **vitrine**, pas une banque : l'achat invité est le comportement
attendu d'un site de démonstration. Ce qui compte pour la grille, c'est que le
paiement soit **réel** et **prouvé**.

Ce qui existe maintenant :

| Question | Réponse |
|---|---|
| Qui achète ? | un nom + un e-mail saisis dans le panier, affichés sur le ticket et dans l'admin |
| Où est l'historique ? | protégé par un **capability token** de 192 bits ; sans lui, 404 |
| Et si je vide le navigateur ? | le jeton est perdu côté client — l'admin, lui, voit tout. C'est une limite assumée |
| Pourquoi pas de compte ? | les exigences N1–N4 demandent vitrine, front, back, paiement et historique — pas un espace client. Un compte est un chantier identifié (~1 j) |

**Phrase à dire** : « La vitrine est publique : c'est un achat invité, comme sur
un site marchand sans création de compte. Le ticket porte l'identité de
l'acheteur, et l'historique est protégé par un jeton de capacité de 192 bits.
L'authentification à deux facteurs protège le back-office, pas le client. »

---

## 5. La carte du back-office — ne plus chercher

La barre du bas est **toujours visible** :

```
   démo [ 1 · Vitrine ] [ 2 · Supervision ] [ 3 · Habilitations ] [ 4 · Admin boutique ] [ 5 · Assistant RAG ]
```

| Bouton | Ce qu'on y trouve | Quand on l'utilise |
|---|---|---|
| **1 · Vitrine** | catalogue, panier, ticket, historique client | 18–38 s |
| **2 · Supervision** | badge d'état, score ML, anneau, console Ansible, audit view-only, **assistant RAG** (plus bas) | 42–57 s, puis Q&A |
| **3 · Habilitations** | comptes, rôles, visage, déblocage, sessions, journal d'audit | Q&A « montrez les rôles » |
| **4 · Admin boutique** | articles, stock, commandes avec client et key image | Q&A « montrez le back » |
| **5 · Assistant RAG** | amène directement au quadrant assistant, question et sources | Q&A « l'IA invente ? » |

Sans session, les boutons 2 à 4 affichent un cadenas et renvoient à la
connexion. C'est normal : le serveur refuserait de toute façon.

**Les trois rôles** (à savoir par cœur) :

| Rôle | Ce qu'il peut faire |
|---|---|
| **admin** | tout : habilitations, sessions, audit, boutique, supervision |
| **operator** | se connecter et voir la supervision ; aucune administration |
| **auditor** | même chose qu'operator aujourd'hui ; la lecture d'audit en lecture seule est un chantier écrit |

> Si le jury demande « montrez ce qu'un auditeur peut faire », la réponse
> honnête est : « aujourd'hui il voit la supervision, pas l'administration ;
> la séparation fine est dans le backlog. » Ne pas improviser.

---

## 6. Les questions qui viennent — réponses courtes

| Question | Réponse (30 s max) |
|---|---|
| « C'est un seuil fixe déguisé ? » | « Non : seuil appris sur 1 287 observations, stocké dans `threshold.json`, et le runtime refuse un seuil venu d'un autre modèle. » |
| « Une seule feature, c'est fragile ? » | « C'est mesuré et physique : gamma 3,6 j contre uniforme 26,9 j, AUC 1,00. Les cinq autres couvrent rejeu, rafale, anneau malformé. » |
| « Votre crypto, c'est du Monero ? » | « Un modèle simplifié mais réel : Ristretto255, key image non forgeable, bLSAG, Pedersen, audit view-only. Deux écarts écrits : pas de CLSAG, pas de Bulletproofs. » |
| « Une photo passe ? » | « Oui, il n'y a pas de vivacité — c'est publié dans `/healthz`. Le mot de passe reste exigé, donc une photo seule ne suffit pas. » |
| « Et si l'objet physique tombe ? » | « Rien : le pont est en lecture seule, aucun abonné SSE n'est obligatoire, aucun chemin ne va du terminal vers le moteur. » |
| « Le SI est invulnérable ? » | « Non. Douze failles fermées, quatre bugs critiques, dix limites écrites. Ce qui est vérifiable, c'est le travail, pas le mot. » |
| « Combien de tests ? » | « 226 : 202 pytest, 5 scénarios, 9 Rust, 10 HTTP. » |
| « Pourquoi pas Prestashop ? » | « Le modèle de données, la surface d'attaque et la preuve sont maîtrisés ; pas de CMS à durcir. » |
| « L'IA invente-t-elle ? » | « Elle cite ses sources. Sans Ollama elle passe en extractif et le dit — elle ne devine pas. » |
| « Quel est le plus gros risque ? » | « La panne silencieuse, pas l'attaque : image sans modèle, webhook cassé, alerte visant le mauvais service. Tous nos contrôles viennent de là. » |

---

## 7. Si ça casse — le geste, la phrase

| Symptôme | Geste | Ce qu'on dit |
|---|---|---|
| Le badge ne bouge pas | l'attaquant relance `poison` | « L'attaque n'a pas porté, je la relance. » |
| Le score monte mais rien | `curl localhost:8083/healthz` → `last_webhook_error` | « Le webhook est cassé, je le signale. » |
| `REMEDIATION_FAILED` | `curl localhost:8084/history` | « Le playbook a échoué, voici la trace. » |
| La caméra ne s'ouvre pas | fermer Zoom/Teams, recharger, vérifier `localhost` | « Une autre application tient la caméra. » |
| Visage non reconnu | se placer face à la lumière, relancer le scan | « Le vidéoprojecteur me met à contre-jour. » |
| Le dashboard est figé | `docker compose restart front-dashboard` | « Le dashboard est un miroir, la chaîne continue. » |
| Plus rien ne répond | `docker compose up -d`, puis `preflight.py` | « Je relance la pile et je vérifie. » |
| Docker ne redémarre pas | **le clip** + captures | « Voici la séquence continue, mesurée hier. » |

**Ne jamais redémarrer `payment-api` en pleine démo** : cela coupe le service
quelques secondes, et le registre est rechargé au démarrage.

---

## 8. Après la démo — 2 minutes

```bash
python tools/attack.py reset     # désarmer la déception
python tools/restock.py          # remettre le stock (si attack.py shop a tourné)
python tools/preflight.py        # doit repasser en code 0
curl -s localhost:8084/history | python -m json.tool   # trace pour le dossier
```

Récupérer les chiffres du jour :

```bash
python tools/e2e_demo.py | tee docs/mesures-soutenance.txt
curl -s localhost:8083/threshold | python -m json.tool
```
