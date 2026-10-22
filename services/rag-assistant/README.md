# rag-assistant — assistant local sur le dossier technique

Réponse à l'exigence du jury « l'IA actualise ses compétences via le RAG » : un
assistant qui répond **sur le dossier du projet**, en citant le document et la
section d'où vient chaque phrase. Aucun modèle n'est entraîné ici ; on
**récupère** les passages utiles, puis on **génère** — ou on cite.

| | |
|---|---|
| Port | **8091** |
| Index | BM25 construit **à la construction de l'image**, jamais au runtime |
| Dépend de | rien dans la pile (Ollama est externe, optionnel) |
| Tests | **16**, sans Ollama (le mode extractif est celui qui est testé) |
| Corpus | `README.md`, `TODO.md`, `docs/`, les README de services et `hardware/README.md` |

---

## 1. Pourquoi un RAG, et pourquoi pas LangChain

La chaîne utile tient en deux étapes : **récupérer** les passages qui parlent de
la question, puis **rédiger** une réponse à partir d'eux. Ici, la récupération
est un BM25 de 40 lignes (sans numpy, sans modèle à télécharger) et la
génération est un appel HTTP à Ollama.

LangChain ajouterait une dépendance lourde et une couche d'indirection pour
deux appels. Une base vectorielle supposerait un modèle d'embedding, donc un
téléchargement au build ou au runtime, et un index à maintenir : trois causes de
panne le jour J pour un corpus de quelques centaines de passages. **Choix
assumé** : un index lexical, construit une fois, figé dans l'image.

Ce que cela garantit :

* l'index est **dans l'image** : aucun téléchargement, aucune indexation au
  premier appel, aucune latence de démarrage à froid ;
* sans Ollama, le service **répond quand même**, en extractif, en citant ses
  sources — un assistant muet en démonstration est pire qu'un assistant qui
  cite le bon paragraphe ;
* chaque réponse porte ses sources : un juré peut vérifier la phrase citée.

---

## 2. Architecture

```
   build de l'image                     runtime
   ┌──────────────────┐          ┌────────────────────────────┐
   │ README, TODO,    │          │  POST /ask                 │
   │ docs/, READMEs   │          │    │                       │
   │      │           │          │    ▼                       │
   │      ▼           │          │  BM25 (top_k passages)     │
   │ build_index.py   │  index   │    │                       │
   │  (sections +     │ ───────► │    ├─ Ollama joignable ──► │ réponse rédigée
   │   fenêtres 900c) │  .json   │    │   (llama3.1:8b)       │ + sources
   └──────────────────┘          │    └─ sinon ──► extractif  │ phrases du dossier
                                 └────────────────────────────┘
```

### Découpage

1. `_split_sections` coupe chaque document sur ses titres Markdown : un passage
   sait toujours de quelle section il vient (`heading`).
2. `_window` découpe les sections en fenêtres de **900 caractères** avec
   **150 de recouvrement**, en coupant sur une fin de phrase quand c'est
   possible.
3. `tokenize` replie les accents (NFKD), passe en minuscules, écarte les mots
   vides français/anglais et les jetons d'un caractère.
4. BM25 `k1 = 1.5`, `b = 0.75` : le classement d'un moteur de recherche, pas une
   similarité approximative.

Les dossiers de dépendances (`node_modules`, `.venv`, `dist`, `target`,
`.pytest_cache`…) sont exclus : sans cela, le README d'un paquet npm se classe
mieux que le dossier technique et l'assistant cite `postcss-value-parser` en
Q&A. Constaté en recette.

### Corpus et chiffres

Le `Dockerfile` copie le corpus depuis la racine du dépôt, puis construit
l'index ; le build **échoue** si l'index a moins de 5 documents ou 100 passages.
L'image de démonstration publie **18 documents** et **~710 passages** — le
nombre exact dépend du contenu des documents au moment du build, et
`/healthz` fait foi après un rebuild.

```bash
# reconstruire l'index à la main (développement)
cd services/rag-assistant
python build_index.py --corpus ../../docs --out index.json
```

---

## 3. Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/ask` | question → réponse + sources + moteur |
| `GET` | `/healthz` | index chargé, nombre de documents/passages, état d'Ollama |
| `GET` | `/metrics` | `rag_index_documents`, `rag_index_chunks` (Prometheus) |

### `POST /ask`

```bash
curl -s -X POST localhost:8091/ask \
  -H 'Content-Type: application/json' \
  -d '{"q":"à quoi sert la key image ?"}'
```

Corps : `q` (3 à 500 caractères, obligatoire), `top_k` (1 à 10, défaut 4).
Une question trop courte est refusée en **422** ; un index absent répond
**503** avec le motif (`reconstruire l'image`).

### Contrat de réponse

```json
{
  "answer": "La key image est le point de contrôle anti-double-dépense : deux signatures du même output produisent la même image, et la seconde est refusée. [docs/RAPPORT.md]",
  "sources": [
    {
      "source": "docs/RAPPORT.md",
      "heading": "Key image et double dépense",
      "score": 9.214,
      "snippet": "…"
    }
  ],
  "engine": "extractive",
  "latency_ms": 3.2
}
```

| Champ | Sens |
|---|---|
| `answer` | réponse rédigée (Ollama) **ou** phrases du dossier, chacune suivie de `[chemin.md]` |
| `sources` | passages réellement utilisés : fichier, section, score BM25, extrait de 280 caractères |
| `engine` | `ollama:<modèle>`, `extractive`, ou `none` si aucun passage ne correspond |
| `latency_ms` | temps de la requête, mesure comprise |

Aucun passage trouvé → `sources: []`, `engine: "none"`, et une phrase explicite
(« Aucun passage du dossier ne traite cette question. »). L'assistant ne devine
pas.

### `GET /healthz`

```json
{
  "status": "ok",
  "service": "rag-assistant",
  "index": { "path": "/app/index.json", "documents": 18, "chunks": 709 },
  "retrieval": "bm25",
  "generation": {
    "model": "llama3.1:8b",
    "ollama_url": "http://host.docker.internal:11434",
    "ollama": { "state": "unreachable", "last_error": "ConnectError", "retry_in_seconds": 27.4 },
    "fallback": "extractive (cite les sources, n'invente pas)"
  },
  "index_built_at": "image"
}
```

La sonde **ne fait aucun appel réseau** : elle lit le disjoncteur. Une version
précédente interrogeait Ollama à chaque `/healthz` avec 1,5 s de timeout ; quand
Ollama était éteint, la sonde Docker et le scrape Prometheus payaient cette
latence en permanence. Mesuré en recette, corrigé, et verrouillé par un test
(5 appels < 500 ms).

---

## 4. Extractive ou Ollama

| Mode | Quand | Ce qui est renvoyé |
|---|---|---|
| `extractive` | Ollama absent, lent, ou disjoncteur ouvert | les phrases du dossier qui couvrent la question, avec leur source |
| `ollama:<modèle>` | Ollama répond | une réponse de trois phrases maximum, citant ses sources |

| Variable | Défaut | Rôle |
|---|---|---|
| `OLLAMA_URL` | `http://host.docker.internal:11434` | adresse du PC IA |
| `OLLAMA_MODEL` | `llama3.1:8b` | modèle (≤ 8 Md de paramètres) |
| `OLLAMA_TIMEOUT` | `45` | lecture ; la **connexion** est bornée à 2 s |
| `RAG_INDEX` | `/app/index.json` | chemin de l'index |
| `RAG_TOP_K` | `4` | passages par défaut |

Trois protections contre un Ollama éteint ou lent :

1. **délais séparés** — connexion 2 s, lecture 45 s (le modèle rédige, c'est
   normal) ;
2. **disjoncteur** — après un échec, plus aucune tentative pendant **30 s** ;
   `/healthz` publie `generation.ollama: unreachable` et `retry_in_seconds` ;
3. **single-flight** — un seul thread sonde Ollama à la fois ; les autres
   repartent immédiatement en extractif. Sans ce verrou, N requêtes simultanées
   lisaient toutes le disjoncteur fermé et saturaient le threadpool de FastAPI.

Le prompt impose le français, trois phrases, **uniquement** les extraits
fournis, et une citation entre crochets après chaque affirmation.

---

## 5. Tests (16)

```bash
cd services/rag-assistant
python -m pytest test_rag.py -q
```

| Ce qui est vérifié | Pourquoi |
|---|---|
| l'index couvre `RAPPORT.md`, `DEMO.md`, > 100 passages | le corpus est bien celui du dossier |
| chaque passage garde son titre de section | une citation sans section n'est pas vérifiable |
| tokenisation : accents repliés, mots vides retirés | « resilience » et « remediation » sont normalisés |
| la question de référence retrouve le bon document | critère d'acceptation de la consigne |
| la réponse extractive **cite** `[docs/RAPPORT.md]` | la réponse est vérifiable, pas inventée |
| la durée mesurée est citée, pas un ordre de grandeur | la valeur vient du dossier, pas du modèle |
| les phrases ne sont pas coupées par le retour à la ligne | les documents sont pliés à 80 colonnes |
| une ligne de tableau devient une phrase | « Durée de la chaîne P0 : … s » |
| une question hors sujet ne trouve rien | pas de faux positif BM25 |
| `/ask` répond `engine: extractive` sans Ollama | le mode dégradé est explicite |
| une question d'un caractère → 422 | validation Pydantic |
| `/healthz` déclare le mode et l'index | le juré lit l'état, pas une promesse |
| 5 `/healthz` < 500 ms Ollama muet | la sonde ne bloque pas |
| le disjoncteur évite de réattendre le timeout | `/ask` reste sous 300 ms |
| une seule sonde passe à la fois | pas de saturation du threadpool |
| remise en état du disjoncteur | les tests ne se contaminent pas |

---

## 6. Limites assumées

| Limite | Pourquoi | Où c'est dit |
|---|---|---|
| **pas de base vectorielle** | BM25 suffit sur un corpus de quelques centaines de passages ; un embedding imposerait un modèle à télécharger et un index à maintenir | ici, §1 |
| **pas de LangChain** | deux appels HTTP ne justifient pas une dépendance lourde et une couche d'indirection | ici, §1 |
| **pas d'authentification sur `/ask`** | endpoint de consultation en lecture seule, sans secret ni écriture ; le dashboard l'appelle depuis le navigateur | ici |
| **injection de prompt possible** | les passages du dossier sont insérés dans le prompt ; un document hostile pourrait contenir des instructions. Le corpus est celui du dépôt, pas une source externe | ici |
| **réponses extractives parfois sèches** | sans Ollama, on cite des phrases ; c'est volontairement vérifiable plutôt que fluide | ici |
| **pas de conversation** | une question, une réponse ; aucun historique de contexte | ici |

Le risque d'injection est réel mais borné : le corpus est versionné dans le
dépôt, l'assistant n'a **aucun outil** (pas de shell, pas de requête SQL, pas
d'accès réseau sortant autre qu'Ollama) et ne peut donc rien exécuter. Une
réponse empoisonnée resterait une phrase fausse citée avec sa source — visible
à la vérification.

---

## 7. Recette

```powershell
# 1. l'index et le mode de génération
curl.exe -s http://localhost:8091/healthz

# 2. une question couverte par le dossier (Invoke-RestMethod : pas d'échappement)
$body = @{ q = "à quoi sert la key image ?" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8091/ask `
  -ContentType "application/json" -Body $body

# 3. une question hors sujet : engine = none, sources = []
$body = @{ q = "météo à Paris" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8091/ask `
  -ContentType "application/json" -Body $body

# 4. les métriques
curl.exe -s http://localhost:8091/metrics
```

Sous macOS/Linux, la même chose en une ligne :

```bash
curl -s -X POST localhost:8091/ask -H 'Content-Type: application/json' \
  -d '{"q":"à quoi sert la key image ?"}'
```

Attendu : `"documents": 18`, `"chunks"` ≈ 710 sur l'image actuelle ; une réponse
qui cite `[docs/…]` et dont `sources` n'est pas vide ; `engine` égal à
`extractive` si Ollama n'est pas joignable. La question de référence de la
consigne (« la durée de la chaîne P0 ») doit retrouver `docs/RAPPORT.md`.
Après un rebuild, `/healthz` fait foi : le nombre de passages dépend du contenu
des documents.

`python tools/preflight.py` contrôle `/healthz` et signale le RAG comme service
**P1** : son absence est un avertissement, pas un blocage de la démonstration.
