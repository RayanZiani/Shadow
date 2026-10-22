# Traite finale — 3 jours pour rendre le TP démontrable et livrable

> **Point de départ** : mardi 20 octobre 2026, fin de journée 2 sur 5.
> **Livraison** : vendredi 23 octobre 2026.
> **État du code** : fonctionnel et testé — 221 tests, 197 pytest verts mesurés
> sur ce poste, 19 services, chaîne P0 en 6,6 / 7,1 / 7,2 s.
>
> **Constat qui commande tout le plan** : le projet n'a plus besoin d'être
> *développé*, il a besoin d'être **prouvé sur le poste de démonstration**,
> **répété** et **livré**. Les trois jours ne servent donc pas à ajouter des
> fonctionnalités : ils servent à transformer « ça marche chez le développeur »
> en « ça marche dans la salle, devant le jury ».

---

## 1. Ce qui est fait, et ce qui reste vraiment

### Fait (avec une preuve exécutable)

| Axe | Livré | Preuve |
|---|---|---|
| Chaîne P0 | attaque → ML → Ansible → preuve → déception | `tools/e2e_demo.py`, 6,6 / 7,1 / 7,2 s |
| Cryptographie | bLSAG, key image, Pedersen, audit view-only | 9 tests Rust + 10 HTTP |
| Boutique (N1–N4) | catalogue PostgreSQL, panier, paiement réel, historique, back d'admin | 36 tests + `tools/e2e_shop.py` |
| IA (N6) | RAG BM25, quadrant assistant, ingestion SIEM, corrélation multi-alertes | 16 tests + 27 tests moteur |
| Sécurité (N5) | 12 failles fermées, 4 bugs critiques fermés, PIN généré, jetons de service | `docs/SECURITE.md`, 43 tests paiement |
| Consigne additionnelle | visage + mot de passe, habilitations, bandeau LED | 29 tests, `tools/backoffice_check.py` |
| Observabilité | Prometheus 12 cibles / 24 règles, Loki 4 règles, Grafana 13 panneaux, Alertmanager → remédiation | `observability/` |
| Documents | rapport, gouvernance, sécurité, charge, démo, règles du jury, consignes | `docs/` |

### Reste (et pourquoi ce n'est pas du code)

| Reste | Nature | Pourquoi ça ne se fait pas ici |
|---|---|---|
| `e2e_shop`, concurrence, persistance, chaos 6/6, charge sous PostgreSQL | **preuve** | Docker absent de ce poste |
| Recalibrage ML sur le PC hôte | **preuve** | dépend du trafic du poste de démonstration |
| Caméra, visage enrôlé, bandeau LED, point d'accès `RESILIENCE-G<n>` | **matériel** | pas de webcam/ESP8266 ici |
| Ollama sur le PC IA | **matériel** | pas de PC IA ici |
| Bulletproofs (XL), vivacité anti-photo (L), TLS (M) | **hors budget** | 3 à 5 jours pour Bulletproofs ; les trois sont écrits comme limites assumées |
| AppRole Vault (S) | **confort** | gain en Q&A, mais risque de casser le playbook la veille |

**Règle de la traite** : à partir du 21/10 à midi, plus aucune fonctionnalité
nouvelle. Uniquement des correctifs prouvés par un test, des mesures, et de la
répétition. Un TP démontrable avec des limites écrites vaut mieux qu'un TP
ambitieux qui plante en salle.

---

## 2. Jour 3 — mercredi 21/10 : la journée Docker (la plus importante)

Objectif de fin de journée : **toutes les preuves qui exigent la pile sont
vertes**, et le modèle est recalibré sur le poste de démonstration.

| Horaire | Tâche | Commande | Critère de fin |
|---|---|---|---|
| 09:00–09:45 | Construire et démarrer la pile sur le PC hôte | `docker compose build` puis `docker compose up -d` | `docker compose ps` : 19 services `Up`/`healthy` |
| 09:45–10:00 | Pré-vol | `python tools/preflight.py` | code 0, aucun contrôle bloquant |
| 10:00–10:30 | Chaîne P0, trois fois | `python tools/e2e_demo.py` ×3 | 3 × `chain_complete: true`, reprise < 90 s |
| 10:30–11:00 | Attaques | `python tools/attack.py all` puis `python tools/attack.py shop` | 4/4 et 6/6 refusées, service debout |
| 11:00–11:30 | Achat de bout en bout | `python tools/e2e_shop.py` | `chain_complete: true`, commande `paid`, stock décrémenté |
| 11:30–12:00 | Persistance + concurrence | `docker compose restart shop-api db-postgres`, relire `/orders` ; deux `curl` simultanés sur le dernier article | historique intact ; une seule commande `paid`, l'autre en `409`, stock ≥ 0 |
| 13:30–14:15 | Chaos | `python tools/chaos.py` | 6/6 `PASS` |
| 14:15–15:00 | Charge sous PostgreSQL | `python tools/load_test.py --target payment --clients 20` puis `--target shop` | pas de `RuntimeError`, courbe boutique cohérente ; mettre `docs/mesures-charge.md` à jour |
| 15:00–15:30 | Loki + alerte SIEM | `docker compose stop monero-crypto-core`, acheter, `curl /alert` | `PaiementRefuse` visible dans Alertmanager et Grafana Explore |
| 15:30–16:00 | Registre et idempotence | `docker compose restart payment-api` ; deux `POST /tx/pay` même `reference` | `registry.durable: true`, `outputs > 0`, une seule key image |
| 16:00–16:45 | Recalibrage ML sur le PC hôte | `capture_baseline.py --seconds 420` puis `train.py --epochs 600`, rebuild `ml-detector` | `model: ready`, `e2e_demo.py` vert |
| 16:45–17:30 | Matériel | enrôlement visage, caméra, PIN noté, firmware flashé, hotspot `RESILIENCE-G1` | `backoffice_check.py` 0 échec, LED verte/rouge conforme |

**Piège à éviter** : ne pas recalibrer le ML **après** avoir enregistré le clip.
Recalibrer change le seuil ; il faut refaire une répétition ensuite.

---

## 3. Jour 4 — jeudi 22/10 : la journée démonstration

Objectif : **le scénario de 60 s passe trois fois de suite, dans la salle, avec
le matériel réel**.

| Horaire | Tâche | Critère de fin |
|---|---|---|
| 09:00–10:00 | Storyboard 60 s (`docs/DEMO.md` §2) au chronomètre, 3 fois | 3 × 60 s sans trou, temps annoncés respectés |
| 10:00–11:00 | Clip Chrono continu, cadrage, lisibilité à 3 m | badge, score ML et anneau lisibles sur le vidéoprojecteur |
| 11:00–12:00 | Répétition Q&A (7 questions de `docs/DEMO.md` §5) | chacun sait quelle question il prend |
| 13:30–15:00 | Ollama sur le PC IA, si disponible | `/ask` → `engine: ollama:llama3.1:8b`, réponse citant `docs/RAPPORT.md` ; sinon on assume l'extractif |
| 15:00–16:00 | AppRole Vault, **seulement si** tout le reste est vert | playbook sans `VAULT_TOKEN`, `jwt_key_source: vault` ; sinon on documente le fallback |
| 16:00–17:30 | Répétition générale en conditions réelles | 2 enchaînements complets, attaquant sur un second poste |

**Décision à prendre ce jour-là** : Ollama et AppRole sont les deux seuls
chantiers « bonus » encore ouverts. S'ils menacent la stabilité, ils sont
abandonnés et **c'est écrit** — c'est exactement ce que le dossier de
gouvernance sait déjà faire.

---

## 4. Jour 5 — vendredi 23/10 : la journée livraison

| Horaire | Tâche | Critère de fin |
|---|---|---|
| 09:00–09:30 | Gel du code | plus aucun commit sauf correctif bloquant |
| 09:30–10:30 | Suite complète | `.\tools\run_tests.ps1` vert, `check_compose.py` vert, `check_encoding.py` vert |
| 10:30–11:15 | Secrets et archive | `git status` vide, aucun `.env`/clé/token Vault dans les fichiers suivis, ZIP sans `node_modules`, `.venv`, `dist` |
| 11:15–12:00 | Documents | PDF du rapport et de la gouvernance, slides à jour, toutes les commandes du README exécutées une fois |
| 13:30–15:00 | Dernière répétition | 1 enchaînement complet, `preflight.py` vert |
| 15:00–16:00 | Plan B | captures d'écran + clip de secours sur clé USB, PC de secours si possible |
| 16:00 | Gel définitif | tag `v1.0-demo` |

---

## 5. Ce qu'on ne fait pas (et ce qu'on répond)

| Chantier | Pourquoi on ne le fait pas | Ce qu'on répond au jury |
|---|---|---|
| **Bulletproofs** | 3 à 5 jours, et change la baseline ML | « sans preuve de domaine, un montant négatif modulo l passe l'équilibre ; c'est écrit dans `/healthz` et dans la gouvernance. C'est la première chose à faire après le workshop. » |
| **Vivacité anti-photo** | nécessite un modèle anti-spoofing ou un challenge vidéo | « le visage est un facteur d'identité, pas un anti-spoofing ; le mot de passe reste le second facteur. Une photo seule ne suffit pas. » |
| **TLS** | la caméra impose `localhost` en démonstration | « en production, TLS + HSTS ; ici le trafic ne quitte pas le PC hôte. » |
| **Monero réel** | hors sujet | « SI fictif, modèle simplifié mais réel sur Ristretto255. » |
| **ModSecurity/CRS** | un proxy de déception sur mesure est plus démontrable | « nous avons écrit le WAF, il déroute vers le honeypot. » |

---

## 6. Risques de la traite, et parade

| Risque | Probabilité | Parade |
|---|---|---|
| Le build Docker échoue sur le PC hôte (torch, cargo, 16 Go) | moyenne | lancer le build **le 21 au matin**, pas la veille au soir ; `docker compose build --parallel` ; garder les images d'un poste qui a déjà construit |
| Le ML faux-positif sur le PC hôte | **élevée** (arrivé 3 fois) | recalibrage 7 min le 21, puis `e2e_demo.py` |
| La caméra est prise par Teams/Zoom | moyenne | fermer les applications, `localhost` obligatoire |
| L'ESP8266 ne joint pas le point d'accès | moyenne | hotspot 2,4 GHz, SSID/mot de passe dans `secrets.h`, vérifier le bail DHCP ; **le bandeau n'est pas dans le chemin critique** |
| Le PIN du jour est inconnu | faible (corrigé) | `TERMINAL_PIN` transmis par le compose ; `preflight.py` le contrôle ; le noter sur une fiche |
| Collision de tables entre services | **fermée** | `payments` → `registry_payments` ; garde-fou dans `check_compose.py` |
| Port 8080/5173 déjà occupé | faible | `netstat -ano | findstr :8080`, arrêter le processus |
| Le jury demande une mesure absente | moyenne | `docs/mesures-charge.md` et `docs/DEMO.md` §5 contiennent les réponses et les chiffres |

---

## 7. Définition de « prêt à démontrer »

Le TP est prêt quand, sur le PC hôte, dans cet ordre :

```powershell
docker compose ps                 # 19 services Up / healthy
python tools/preflight.py         # code 0
python tools/e2e_demo.py          # chain_complete: true
python tools/e2e_shop.py          # chain_complete: true
python tools/attack.py shop       # 6/6 refusées
python tools/backoffice_check.py  # 0 échec
.\tools\run_tests.ps1             # tout vert
```

et quand le scénario de `docs/DEMO.md` §2 tient **60 secondes, sans coupe, trois
fois de suite**, avec la caméra et le bandeau LED branchés.

Tant qu'une de ces lignes est rouge, la traite n'est pas finie.
