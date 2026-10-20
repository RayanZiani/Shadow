# TODO — ce qui reste à faire

> État au 20 octobre 2026. Le MVP est fonctionnel et mesuré (79 tests verts,
> chaîne P0 en 6,6-7,2 s, 14 services `Up`).
> Ce fichier liste **uniquement ce qui n'est pas fait**, avec un critère
> d'acceptation vérifiable pour chaque ligne.

Légende : `[ ]` à faire · `[~]` partiel · `[x]` fait
Effort : **S** ≤ 2 h · **M** ≤ 1 j · **L** ≤ 3 j · **XL** > 3 j

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

---

## 1. Priorité haute — points perdus à la grille

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
| Pas de `pre-commit` | 79 tests suffisent | optionnel |

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
Semaine 1   §0 (recalibrage + 3 répétitions)   ← bloquant, 1 h
            1.2 AppRole Vault                   ← ½ j, gain immédiat en Q&A
            2.2 observabilité propre            ← ½ j, évite un signal négatif
Semaine 2   1.3 registre PostgreSQL             ← 2 j, supprime un risque de démo
            1.1 Bulletproofs                    ← 3-5 j, seule faille crypto
Semaine 3   1.4 RAG Ollama                      ← 2 j, complète le pilier IA/DATA
            2.3 + 2.4 charge et chaos           ← 1 j, matière pour le dossier
Semaine 4   §3 objet physique                   ← 1 j, non bloquant
            Passe finale : §0                   ← obligatoire la veille
```

**Si le temps manque** : §0, puis 1.2, puis 2.5. Ce sont les trois lignes qui
coûtent le moins et qui rapportent le plus (respectivement : ne pas perdre la
démo, l'axe 2, un argument visuel pour le clip).
