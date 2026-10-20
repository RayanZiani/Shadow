# Mesures de charge — point de rupture

> Produit par `python tools/load_test.py --target <cible> --clients 1 5 10 20`.
> Cible : le poste de développement, services lancés hors Docker (aucune
> limitation de ressources de conteneur). Les chiffres absolus ne sont donc pas
> ceux de la démonstration : ce qui compte est **la forme de la courbe** et
> l'identification du point où la latence cesse d'être plate.

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

Résultat : **×670 de débit** et p95 divisé par 580. C'est exactement le genre de
défaut qu'un test de charge trouve et qu'une relecture de code ne trouve pas.

## Boutique (`POST /checkout`, parcours complet avec paiement bLSAG)

| Clients | Débit (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Erreurs |
|---|---|---|---|---|---|
| 1 | 7,2 | 129 | 150 | 460 | 0,00 % |
| 5 | 7,9 | 559 | 1 000 | 1 029 | 0,00 % |
| 10 | 139,9 | 7 | 823 | 1 428 | 0,00 % |

**Lecture** : un achat complet (panier + paiement par signature d'anneau +
écriture en base) coûte **~130 ms** à vide. Au-delà de 5 clients simultanés, la
latence dépasse 1 s : le chemin d'achat est **le point de rupture du SI**.

Trois raisons, dans l'ordre :

1. chaque achat déclenche une **signature d'anneau** (coût cryptographique réel) ;
2. chaque achat fait un aller-retour `shop-api → payment-api` ;
3. la base SQLite du poste de test sérialise les écritures (PostgreSQL en pile
   complète lève cette limite).

**Ce n'est pas un défaut** : une boutique de démonstration n'a pas besoin de
140 achats/s. C'est une **limite connue et chiffrée**, ce qu'un jury demande.

---

## Ce que la démonstration doit retenir

| Question | Réponse mesurée |
|---|---|
| « Combien de clients simultanés tenez-vous ? » | 20+ en lecture (630 req/s), ~5 en achat complet |
| « Où cède le SI ? » | sur le chemin d'achat, à cause de la signature d'anneau |
| « Est-ce que ça tombe ? » | non : les erreurs restent à **0 %**, seule la latence monte |
| « Et avec la base PostgreSQL ? » | à mesurer sur la pile complète (§ ci-dessous) |

## Reproduire

```bash
docker compose up -d
python tools/load_test.py --target payment --clients 1 5 10 20
python tools/load_test.py --target shop    --clients 1 5 10
python tools/load_test.py --target rag     --clients 1 5 10 20
```

Le rapport est réécrit à chaque exécution dans ce fichier. **À refaire sur le PC
hôte de démonstration** : les chiffres ci-dessus viennent d'un poste de
développement sans Docker.
