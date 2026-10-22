# Préparer la soutenance — ce poste est le PC Hôte **et** le PC IA

> État mesuré le **22 octobre 2026** sur ce poste (Mac Apple Silicon, 36 Go,
> Colima + Docker 29.5, Ollama local) : **19 services Up**, pré-vol vert,
> chaîne P0 **7,2 / 7,4 / 6,7 s** sur trois exécutions consécutives, boutique
> `paid` en 72 ms, **6/6** scénarios de chaos, **221 tests** verts, RAG en
> `ollama:llama3.1:8b`.
>
> Ce document dit **ce qui reste à faire**, dans l'ordre, pour transformer cet
> état en soutenance. Le déroulé seconde par seconde est dans
> [`DEMO.md`](DEMO.md) ; le second poste dans [`PC-ATTAQUANT.md`](PC-ATTAQUANT.md).

---

## 1. Ce qui est prêt, et ce qui ne l'est pas

### Prêt et vérifié sur ce poste

| Preuve | Commande | Résultat du 22/10 |
|---|---|---|
| Pile complète | `docker compose ps` | 19 services `Up` / `healthy` |
| Pré-vol | `python tools/preflight.py` | 21 OK, 0 échec, « DÉMONTRABLE » |
| Chaîne P0 ×3 | `python tools/e2e_demo.py` | `chain_complete: true`, 7,2 / 7,4 / 6,7 s |
| Boutique | `python tools/e2e_shop.py` | commande `paid`, stock décrémenté, key image stockée |
| Attaques | `python tools/attack.py all` · `shop` | 4/4 et 6/6 conformes, service debout |
| Chaos | `python tools/chaos.py` | 6/6 `PASS` |
| Tests | `./tools/run_tests.sh --fast` | 221 verts (dont Rust + HTTP) |
| IA locale | `python tools/warmup_rag.py` | `ollama:llama3.1:8b`, sources citées |
| Observabilité | `curl localhost:9090/api/v1/targets` | 12 cibles `up` |
| Grafana | `localhost:3001` | sources Prometheus **et** Loki provisionnées |

Le modèle ML a été **recalibré sur ce poste** (1 287 observations, seuil appris
**6,715**, FPR 0 %, rappel 100 %) : c'est la tâche qui aurait fait échouer la
démo si elle avait été oubliée.

### Ce qui bloque encore la démonstration

| # | À faire | Pourquoi c'est bloquant | Temps |
|---|---|---|---|
| **1** | **Enrôler l'administrateur** (visage + mot de passe) | `bootstrap_required: true` : le dashboard **n'affiche rien** sans session. C'est aussi le parcours de la consigne additionnelle (E1–E9) | 5 min |
| **2** | **Vérifier la caméra dans le navigateur** | `getUserMedia` ne fonctionne qu'en `http://localhost:5173` ; autorisation à accorder une fois | 2 min |
| **3** | **Enregistrer le Clip Chrono** (60 s, sans coupe) | livrable noté 15 pts, et le seul obligatoire en vidéo | 1 h |
| **4** | **Écrire le PPTX** | livrable obligatoire, aucun support n'existe encore dans `docs/` | 2 h |
| **5** | **Dossier d'ingénierie en PDF** (8–12 pages hors annexes) | livrable obligatoire ; `RAPPORT.md` fait 844 lignes, il faut en extraire 10 pages | 1 h 30 |
| **6** | **ZIP du code sans secret** | règle éliminatoire | 30 min |
| 7 | Bandeau LED ESP8266 + point d'accès | E10–E16 : **non bloquant par conception**, mais 6 exigences ne sont prouvées que sur la carte | 1 h si le matériel est là |

**Ordre recommandé** : 1 → 2 → 3 → 4 → 5 → 6. Le clip avant les slides : il
impose les chiffres et les captures qui iront dans le PPTX.

---

## 2. Étape 1 — enrôler l'administrateur (5 minutes)

Sans cette étape, `http://localhost:5173` affiche l'écran de connexion et
**aucun quadrant** n'est visible : ni badge d'état, ni score ML, ni console
Ansible. C'est le premier geste à faire, et il se refait sur tout poste neuf.

```bash
# 1. le jeton d'enrôlement, en terminal PRIVÉ (il ne doit pas être projeté)
grep '^BO_BOOTSTRAP_TOKEN=' .env

# 2. le service tourne avec ce jeton
docker compose ps backoffice
curl -s localhost:8086/healthz | python -c "import json,sys; d=json.load(sys.stdin); print(d['bootstrap_required'], d['face_engine'])"
# attendu : True ready
```

Puis **dans le navigateur du PC hôte** (jamais par une IP LAN : la caméra serait
refusée) :

1. ouvrir `http://localhost:5173` ;
2. écran d'enrôlement : identifiant, mot de passe (12 caractères ou plus),
   jeton, puis **scan du visage** ;
3. accepter la demande d'accès caméra (macOS : *Réglages → Confidentialité et
   sécurité → Caméra* si le navigateur ne la demande pas) ;
4. après le vert fixe, **retirer le jeton** de `.env` et recréer le service :

```bash
# commenter ou vider BO_BOOTSTRAP_TOKEN dans .env, puis
docker compose up -d --force-recreate backoffice
python tools/backoffice_check.py      # 0 échec attendu
curl -s localhost:8086/healthz | python -c "import json,sys; d=json.load(sys.stdin); print(d['bootstrap_required'], d['demo_only'])"
# attendu : False False
```

> **Le mot de passe de démonstration se note sur une fiche papier**, comme le
> PIN du terminal : ni dicté, ni affiché, ni collé dans un chat.

Si le visage n'est pas reconnu à la connexion : se placer face à la fenêtre
(le vidéoprojecteur met à contre-jour), visage entier dans le cadre, puis
relancer le scan. Deux échecs = 5 minutes de blocage — c'est une
fonctionnalité, pas une panne, et un administrateur peut débloquer depuis
« Habilitations ».

---

## 3. Étape 2 — le parcours de démonstration, tel qu'il se voit

Le storyboard complet est [`DEMO.md`](DEMO.md) §2. Ce qui change **sur ce
poste**, par rapport au runbook Windows :

| Sujet | Sur ce poste |
|---|---|
| Adresse du dashboard | `http://localhost:5173` (jamais `10.251.4.134:5173` : la caméra exige un contexte sécurisé) |
| Ollama | déjà installé et démarré (`brew services start ollama`), modèle `llama3.1:8b` téléchargé |
| PIN du terminal | généré dans `.env` (`TERMINAL_PIN`) — le lire en terminal privé et le noter |
| Grafana | `admin` / `admin` (volume créé au premier démarrage) |
| Attaquant | second poste, `SHADOW_HOST=<IP de ce Mac>` — voir `PC-ATTAQUANT.md` |
| Bandeau LED | absent : le **miroir à l'écran** remplace le bandeau (même état, même cadence) |

Avant d'entrer dans la salle, quatre gestes macOS qui évitent 80 % des
incidents :

```bash
# 1. empêcher la mise en veille pendant la soutenance
caffeinate -dimsu &            # Ctrl-C ou kill %1 après

# 2. vérifier la pile et le modèle (le modèle doit être chaud)
docker compose ps
python tools/preflight.py
python tools/warmup_rag.py     # paie le chargement des 4,9 Go d'Ollama

# 3. remettre l'état de départ
python tools/attack.py reset
python tools/restock.py

# 4. mode Ne pas déranger + zoom navigateur 125 %
```

Et deux réglages système :

- **Ne pas déranger** activé (aucune notification ne doit apparaître dans le
  clip) ;
- **alimentation** branchée, économiseur d'écran désactivé.

---

## 4. Étape 3 — le Clip Chrono (60 s, sans coupe)

C'est la pièce la plus rentable : 15 points sur 70, et elle sert de preuve si la
démo live échoue.

**Ce qu'on enregistre** : l'écran du PC hôte **plus** le terminal du PC
Attaquant dans le même cadre, si possible (deux fenêtres côte à côte). Le clip
doit contenir, dans cet ordre, les 60 s du §2 de `DEMO.md` : authentification
visage → vitrine → panier → paiement réel → attaque → ML → Ansible → reprise →
déception.

| Réglage | Valeur |
|---|---|
| Outil | `Cmd+Shift+5` (macOS), ou OBS si disponible |
| Zone | écran entier, 1920×1080 ou la résolution native |
| Audio | micro si vous commentez en direct ; sinon muet et voix off au montage |
| Durée | 60 s **exactement**, une seule prise, aucune coupe |
| Format | MP4 H.264, ≤ 100 Mo (ffmpeg est installé : `ffmpeg -i in.mov -c:v libx264 -crf 20 out.mp4`) |

**Trois éléments doivent être lisibles à 3 mètres** : le badge d'état, le score
ML avec son seuil, et la console Ansible. Vérifier sur le vidéoprojecteur, pas
sur l'écran du portable.

**Tourner trois fois** : une prise « propre », une de secours, et une où
l'attaque est relancée à la fin (déception). Garder la meilleure sur le bureau
**et** sur une clé USB.

> Ne pas oublier `python tools/restock.py` entre deux prises : l'attaque de
> survente (`attack.py shop`) vide réellement un article, et une vitrine en
> « rupture » dans le clip se remarque.

---

## 5. Étape 4 — le PPTX (plan de 14 diapositives)

Grille nationale (70 pts) : démo live 25 · Clip 15 · pitch 10 · expertise 10 ·
gouvernance 10. Grille locale (20 pts) : démo 5 · piliers 4 · clip/dashboard 4 ·
gouvernance 4 · dossier/Q&A 3. **Chaque diapositive ci-dessous répond à un axe.**

| # | Titre | Contenu (une idée, un chiffre, une preuve) | Axe |
|---|---|---|---|
| 1 | RESILIENCE-ALPHA / SHADOW-SETTLE | paiement fictif à confidentialité cryptographique, détection ML, remédiation autonome. Équipe, rôles | pitch |
| 2 | Le problème | une plateforme qui tombe et qu'on relance à la main : lent, non reproductible, non prouvé. La question : « le vôtre saura-t-il se relever seul ? » | pitch |
| 3 | La réponse en une image | l'architecture de `RAPPORT.md` §2.1 : PC Attaquant → WAF → paiement → noyau Rust, ML → moteur → Ansible → déception | expertise |
| 4 | Les 5 piliers, reliés | DEV · INFRA · IA/DATA · CYBER · TRANSFORMATION, avec **le flux** qui les relie (pas les logos) | piliers |
| 5 | La crypto est réelle | Ristretto255, key image `I = x·H_p(P)`, bLSAG, Pedersen, audit view-only. Les 2 écarts assumés (CLSAG, Bulletproofs) | expertise |
| 6 | La détection est apprise | 6 features, seuil appris **6,715**, FPR 0 %, AUC **1,000** sur `decoy_median_age_days`, justification physique (gamma 3,6 j vs uniforme 26,9 j) | expertise |
| 7 | La remédiation est autonome | les 10 étapes du playbook, **0 intervention humaine**, reprise **7,2 s** (limite 90 s) | démo |
| 8 | La preuve, pas la promesse | 3 exécutions consécutives, transaction de preuve, attaquant dérouté, `ansible_rc: 0` | démo |
| 9 | Le SI commerce (règles du 20/10) | vitrine PostgreSQL 6 articles, panier, `paid` en 72 ms, key image stockée, historique par capability token, back d'admin | expertise |
| 10 | L'IA qui agit | RAG BM25 18 documents / ~710 passages, sources citées, `ollama:llama3.1:8b` ; SIEM Prometheus/Loki → Alertmanager → `/alert` → même playbook | expertise |
| 11 | Sécurité : 12 failles fermées | F1–F12 + 4 bugs critiques, chacune avec son test ; 6 familles d'attaques rejouables en une commande | expertise |
| 12 | Gouvernance | EBIOS RM (7 scénarios), RTO 15 s / RPO, NIS2 art. 21, DORA art. 19, RGPD biométrie | gouvernance |
| 13 | Ce qui reste ouvert | Bulletproofs, vivacité anti-photo, TLS, AppRole, PostgreSQL mono-nœud, Ollama (branché ici) — **écrit, daté, priorisé** | gouvernance |
| 14 | Conclusion | « un système qui échoue bruyamment vaut mieux qu'un système qui réussit en silence » + les 3 chiffres à retenir | pitch |

**Règles de forme** (charte du sujet, slide 9) : Montserrat, écriture `#261E48`,
fond `#E4E6F3`, rectangles `#A1A6D4`. Les couleurs d'état (rouge/orange/vert)
sortent volontairement de la charte : elles doivent être lisibles à distance.

**Ce qu'on met en capture d'écran** : le dashboard en état `SOUS ATTAQUE`, la
console Ansible, le quadrant RAG avec ses sources, Grafana Explore sur Loki.
Quatre images, pas plus.

---

## 6. Étape 5 — le dossier PDF (8 à 12 pages hors annexes)

Le contenu existe déjà : `docs/RAPPORT.md` (844 lignes) + `GOUVERNANCE.md`
(570 lignes). Il faut **extraire**, pas tout envoyer.

| Page | Contenu | Source |
|---|---|---|
| 1 | Page de garde + résumé (5 chiffres) | ce document §1 |
| 2 | Problème et périmètre | `RAPPORT.md` §1 |
| 3–4 | Architecture + 5 piliers + décisions structurantes | `RAPPORT.md` §2.1–2.3 |
| 5 | Détection ML : pipeline, mesures, ce que la mesure a imposé | `RAPPORT.md` §3 |
| 6 | Cryptographie : primitives, écarts assumés | `RAPPORT.md` §4 |
| 7 | Remédiation + déception : les 10 étapes | `RAPPORT.md` §5 |
| 8 | Preuves : 3 exécutions, attaques, 221 tests, charge, chaos | `RAPPORT.md` §6 |
| 9 | Limites | `RAPPORT.md` §7 |
| 10 | Gouvernance : EBIOS, PCA/PRA, NIS2, DORA | `GOUVERNANCE.md` §4–8 |
| Annexes | endpoints, commandes de reproduction, volumétrie | `RAPPORT.md` annexes A–C |

Conversion : `pandoc` n'est pas installé. Le plus simple ici est
`npx @marp-team/marp-cli` ou VS Code + *Markdown PDF*. Si vous installez
pandoc : `brew install pandoc basictex` puis
`pandoc docs/RAPPORT.md -o dossier.pdf --pdf-engine=xelatex -V geometry:margin=2cm`.

**Ne pas oublier** : nom du dossier `Workshop2026-M2-G<n>`, et la contribution
de chacun (axe 5 de la grille locale : « contribution de chacun visible »).

---

## 7. Étape 6 — le ZIP et la règle éliminatoire

```bash
git status --short                       # doit être vide après commit
git grep -n -E "hvs\.|BEGIN [A-Z ]*PRIVATE KEY" -- .   # aucune ligne
python tools/check_encoding.py           # aucun mojibake
```

Le ZIP contient le dépôt **sans** `.env`, `secrets/`, `.venv/`, `node_modules/`,
`dist/`, `target/` :

```bash
git archive --format=zip --prefix=Workshop2026-M2-G1/ -o Workshop2026-M2-G1.zip HEAD
```

`git archive` exclut par construction tout ce qui n'est pas suivi : c'est la
garantie qu'aucun `.env` ni `secrets.h` ne parte. Vérifier ensuite le contenu :

```bash
unzip -l Workshop2026-M2-G1.zip | grep -E '\.env|secrets\.h|node_modules' # doit être vide
```

---

## 8. Étape 7 — répétition générale (J-1, puis J-0)

```bash
# 0. la veille : démarrer Colima et la pile, puis tout vérifier
colima start --cpu 4 --memory 8      # si la VM n'est pas déjà lancée
docker compose up -d
python tools/preflight.py            # DÉMONTRABLE
python tools/warmup_rag.py           # Ollama chaud

# 1. trois répétitions consécutives de la chaîne P0
for i in 1 2 3; do python tools/e2e_demo.py; python tools/attack.py reset; done

# 2. la boutique et les attaques
python tools/e2e_shop.py
python tools/attack.py shop
python tools/restock.py

# 3. le second poste
#    (sur le PC Attaquant) SHADOW_HOST=<IP> python tools/attack.py poison

# 4. la salle : vidéoprojecteur, lisibilité à 3 m, clip relu en entier
```

Critère de fin : **trois** `chain_complete: true`, `preflight.py` en code 0, et
le clip regardé une fois sur le vidéoprojecteur.

---

## 9. Risques propres à ce poste, et parade

| Risque | Probabilité | Parade |
|---|---|---|
| Colima arrêté (redémarrage du Mac) | moyenne | `colima start --cpu 4 --memory 8`, puis `docker compose up -d` ; garder cette ligne sur la fiche |
| Mac en veille pendant la démo | moyenne | `caffeinate -dimsu`, alimentation branchée, écran réglé sur « jamais » |
| Notification macOS dans le clip | élevée | Ne pas déranger activé avant l'enregistrement |
| Caméra prise par une autre app | moyenne | fermer Zoom/Teams/FaceTime ; recharger la page |
| Page ouverte par l'IP LAN | moyenne | toujours `http://localhost:5173` |
| Ollama déchargé (5 min d'inactivité) | moyenne | `warmup_rag.py` juste avant ; poser une question dans le quadrant RAG avant le jury |
| Premier appel Ollama lent (10 s) | certaine si non préchauffé | idem : le préchauffage est la parade |
| Hotspot `RESILIENCE-G<n>` (E17) | moyenne | macOS fait du partage Internet, pas un vrai point d'accès : tester l'ESP **la veille**, sinon le dire comme limite (le bandeau n'est jamais dans le chemin critique) |
| Port 5173 / 8080 déjà occupé | faible | `lsof -nP -iTCP:5173 -sTCP:LISTEN` |
| Le PIN du jour est inconnu | faible | noté sur la fiche ; `preflight.py` vérifie qu'il est transmis |

---

## 10. Ce qu'on dit, ce qu'on ne dit pas

| On dit | On ne dit pas |
|---|---|
| « 12 failles fermées, 4 bugs critiques, chacune avec son test » | « invulnérable » |
| « 6 familles d'attaques rejouables en une commande » | « on a tout couvert » |
| « le modèle est recalibré sur ce poste, FPR 0 % » | « ça marche partout » |
| « 7,2 s de reprise, limite 90 s » | « instantané » |
| « Bulletproofs absentes : un montant négatif modulo l passerait » | silence, puis « on verra » |
| « la photo passe le facteur visage, le mot de passe reste exigé » | « MFA complet » |
| « RAG extractif si Ollama tombe, et il le dit » | « l'IA ne se trompe jamais » |

**La règle qui rapporte des points** : une limite écrite et datée ne coûte
rien ; une limite découverte en Q&A en coûte. Les 10 limites sont dans
[`SECURITE.md`](SECURITE.md) §3, les 10 points ouverts dans `TODO.md` §8.

---

## 11. Plan B

| Panne | Geste | Ce qu'on dit |
|---|---|---|
| La démo live ne part pas | `python tools/preflight.py`, lire « NON DÉMONTRABLE » | « je vérifie le pré-vol » |
| Un service manque | `docker compose up -d <service>` | « je relance le service » |
| Plus rien ne répond | `docker compose up -d` puis `preflight.py` | « je relance la pile » |
| Docker/Colima ne redémarre pas | **le clip** + captures d'écran sur clé USB | « voici la séquence continue, mesurée hier » |
| Le PC attaquant ne joint pas l'hôte | lancer `attack.py poison` depuis le PC hôte (le code est le même) | « l'attaque part du même poste, le résultat est identique » |
| La caméra refuse | montrer le refus : c'est le comportement attendu sur un visage inconnu | « la décision est côté serveur » |

Le clip et les captures sont la seule assurance qui ne dépend d'aucun service :
les préparer **avant** le reste.
