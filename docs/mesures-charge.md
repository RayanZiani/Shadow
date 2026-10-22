### Test de charge — `rag`

Seuils de rupture : p95 > 1000 ms ou erreurs 5xx > 1 %.

| Clients | Débit (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Erreurs | Requêtes |
|---|---|---|---|---|---|---|
| 1 | 20.8 | 48 | 52 | 52 | 0.00 % | 104 |
| 5 | 99.9 | 48 | 56 | 62 | 0.00 % | 504 |

**Aucune rupture jusqu'à 5 clients.** Débit maximal mesuré : 99.9 req/s (5 clients, p95 = 56 ms).
