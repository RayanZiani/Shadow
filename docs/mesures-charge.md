# Mesures de charge — débit, latence, point de rupture

> Produit par `python tools/load_test.py --target <cible> --clients ...`.
> **Paliers** : paiement et RAG `1 5 10 20` ; boutique `1 5 10` (le parcours
> d'achat est plus lourd, le palier 20 n'a pas été exécuté).
>
> **Note de méthode.** Mesures faites sur un **poste de développement**, services
> lancés **hors Docker** (aucune limitation de ressources de conteneur) et
> **sans PostgreSQL** : la boutique tourne sur SQLite (fichier local). Conséquence
> directe : pour la boutique, la **latence est un majorant** et le **débit un
> minorant** par rapport à la pile de démonstration. Les chiffres absolus ne sont
> donc pas ceux du jour J ; ce qui compte est **la forme de la courbe** et
> l'identification du point où la latence cesse d'être plate. Aucun chiffre n'est
> extrapolé d'un poste à l'autre.

Seuils retenus : **p95 > 1 000 ms** ou **> 1 % d'erreurs 5xx**. Ce sont des
choix, pas des constantes du système.

---

## API de paiement (`GET /outputs?unspent=true`)

| Clients | Débit (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Erreurs |
|---|---|---|---|---|---|
| 1 | 466,8 | 2 | 2 | 3 | 0,00 % |
| 5 | 614,3 | 8 | 8 | 16 | 0,00 % |
| 10 | 610,6 | 15 | 17 | 26 | 0,00 % |
| 20 | 631,6 | 29 | 32 | 40 | 0,00 % |

**Lecture** : pas de rupture jusqu'à 20 clients. Le débit plafonne vers
**630 req/s** (le serveur est saturé à 5 clients, la latence augmente ensuite
linéairement). C'est le comportement attendu d'un service mono-processus :
le débit sature, la latence file.

## Assistant RAG (`POST /ask`, mode extractif, Ollama éteint)

| Clients | Débit (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Erreurs |
|---|---|---|---|---|---|
| 1 | 106,5 | 4 | 4 | 6 | 0,00 % |
| 5 | 351,2 | 13 | 15 | 17 | 0,00 % |
| 10 | 338,6 | 27 | 32 | 36 | 0,00 % |
| 20 | 336,5 | 53 | 61 | 379 | 0,00 % |

**Ce que la mesure a corrigé** : le premier passage donnait **0,5 req/s** et
**2 318 ms de p95 avec un seul client**. Cause : `/ask` attendait le timeout
d'Ollama à chaque appel, et `/healthz` interrogeait Ollama avec un timeout de
1,5 s. Deux corrections :

1. **disjoncteur** : après un échec, on ne retente plus pendant 30 s ;
2. **`/healthz` sans appel réseau** : il lit le disjoncteur, réponse instantanée.

Résultat : **×670 de débit** (0,5 → 336 req/s) et, sur un seul client, un p95
divisé par ~580 (2 318 → 4 ms). C'est exactement le genre de défaut qu'un test de
charge trouve et qu'une relecture de code ne trouve pas.

## Boutique (`POST /checkout`, parcours complet avec paiement bLSAG)

| Clients | Débit (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Erreurs |
|---|---|---|---|---|---|
| 1 | 7,2 | 129 | 150 | 460 | 0,00 % |
| 5 | 7,9 | 559 | 1 000 | 1 029 | 0,00 % |
| 10 | 139,9 | 7 | 823 | 1 428 | 0,00 % |

**Anomalie de mesure — à ne pas masquer.** Le palier 5 clients donne **7,9 req/s
et p50 559 ms** ; le palier 10 donne **139,9 req/s et p50 7 ms**. Un débit
multiplié par 18 quand la concurrence double n'est pas plausible : soit une
erreur de transcription, soit un artefact du poste de mesure (SQLite, écritures
sérialisées, cache). Les chiffres bruts sont conservés tels quels ; **aucun
chiffre n'est reconstitué**. La pile n'est pas disponible sur ce poste pour
re-mesurer.

**Conclusion prudente.** Le **point de rupture annoncé à 5 clients n'est pas
établi par ce tableau**. Ce qui est établi : un achat complet (panier + paiement
par signature d'anneau + écriture en base) coûte **~130 ms** à vide, et le taux
d'erreur reste à **0 %**. Mesure **à refaire sur le PC hôte avec PostgreSQL**.

Trois raisons au coût unitaire, dans l'ordre :

1. chaque achat déclenche une **signature d'anneau** (coût cryptographique réel) ;
2. chaque achat fait un aller-retour `shop-api → payment-api` ;
3. la base SQLite du poste de test sérialise les écritures (PostgreSQL en pile
   complète lève cette limite).

**Ce n'est pas un défaut** : une boutique de démonstration n'a pas besoin de
140 achats/s. C'est une **limite connue et chiffrée**, ce qu'un jury demande —
à condition de ne pas annoncer un point de rupture que la mesure ne prouve pas.

---

## Ce que la démonstration doit retenir

| Question | Réponse mesurée |
|---|---|
| « Combien de clients simultanés tenez-vous ? » | 20+ en lecture (630 req/s) ; pour l'achat complet, **non établi** par ce tableau (coût unitaire ~130 ms) |
| « Où cède le SI ? » | sur le chemin d'achat, à cause de la signature d'anneau ; palier exact à remesurer |
| « Est-ce que ça tombe ? » | non : les erreurs restent à **0 %**, seule la latence monte |
| « Et avec la base PostgreSQL ? » | à mesurer sur la pile complète (§ « Ce qu'il reste à mesurer ») |

---

## Chaos engineering

`tools/chaos.py` injecte six pannes et vérifie un **invariant métier**, pas un
code HTTP. Le script redémarre systématiquement ce qu'il a arrêté (`finally`) ;
sans Docker, il déclare `SKIP` avec la raison au lieu de prétendre avoir testé.

| Scénario | Panne injectée | Invariant métier | Statut réel |
|---|---|---|---|
| `token` | aucun conteneur ; appels d'administration sans jeton | **503/401 fail-closed**, jamais 200 ni 500 | **vérifié sur ce poste** |
| `ml` | `docker compose stop ml-detector` | le paiement continue (le ML observe) | à passer sous Docker |
| `remediation` | `stop remediation-engine` | le paiement reste intact, `/state` injoignable comme attendu | à passer sous Docker |
| `crypto` | `stop monero-crypto-core` | `/tx/pay` → **503/502 explicite**, `/healthz` dit `crypto_core=degraded` | à passer sous Docker |
| `shop` | `stop shop-api` | `payment-api` reste sain (silos indépendants) | à passer sous Docker |
| `rag` | `stop rag-assistant` | ni la boutique ni le paiement ne s'en aperçoivent | à passer sous Docker |

**Statut : 1 scénario vérifié en réel (`token`), 5 non exécutés** faute de
Docker. Aucun résultat n'est inventé : les cinq autres sont à passer sur le PC
hôte (`python tools/chaos.py`).

---

## Ce qu'il reste à mesurer

| À mesurer | Pourquoi | Comment |
|---|---|---|
| **Boutique avec PostgreSQL + Docker** | SQLite sérialise les écritures ; le point de rupture du tableau est incohérent | `docker compose up -d`, puis `python tools/load_test.py --target shop --clients 1 5 10 20` |
| **Les 5 scénarios de chaos** | ils arrêtent des conteneurs, donc exigent la pile | `python tools/chaos.py` sur le PC hôte |
| **La pile complète de 19 services** | les mesures ci-dessus isolent des cibles ; la charge réelle traverse le WAF, la boutique et la base | `python tools/preflight.py` puis charge sur la pile |
| **Le poste de démonstration** | les chiffres viennent d'un poste de développement sans Docker | refaire les trois cibles sur le PC hôte et remplacer ce fichier |

## Reproduire

```bash
docker compose up -d
python tools/load_test.py --target payment --clients 1 5 10 20
python tools/load_test.py --target shop    --clients 1 5 10
python tools/load_test.py --target rag     --clients 1 5 10 20
python tools/chaos.py
```

**Attention** : `load_test.py` **réécrit ce fichier** avec le seul rapport de la
cible exécutée. Sauvegardez cette version avant de relancer, puis fusionnez le
nouveau tableau.

**À refaire sur le PC hôte de démonstration** : les chiffres ci-dessus viennent
d'un poste de développement sans Docker ni PostgreSQL.
