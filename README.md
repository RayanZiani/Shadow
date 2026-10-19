# RESILIENCE-ALPHA — SHADOW-SETTLE

Squelette de départ pour le workshop. Rien ici n'est fini : chaque service
contient des `TODO (vous)` aux endroits précis où brancher votre logique
réelle (CLSAG, entraînement ML, config WAF, Vault). Objectif : que vous
puissiez `docker compose up` dès ce soir et avancer brique par brique.

## Démarrage rapide

```bash
cp .env.example .env            # puis éditez .env — ne commitez jamais ce fichier
docker compose up db-postgres vault -d
docker compose up monero-crypto-core payment-api auth-service -d
docker compose up ml-detector remediation-engine shadow-api -d
docker compose up front-dashboard -d
```

Testez le noyau Rust dès qu'il compile :
```bash
curl -X POST localhost:8081/crypto/stealth-address \
  -H "Content-Type: application/json" \
  -d '{"public_spend_key_hex":"...","public_view_key_hex":"..."}'
```

## Ce qui est écrit vs. ce qui reste à faire

| Service | Statut |
|---|---|
| `monero-crypto-core` | Stealth-address réel (ECDH Ristretto). Ring-sign/verify/audit-view = **squelette à remplir avec votre code de wallet** |
| `payment-api` | Fonctionnel (decoys, submit, anti-double-dépense, auto-probe). Decoys = tirage uniforme, à remplacer par une distribution gamma |
| `auth-service` | JWT fonctionnel, lecture Vault à implémenter |
| `shadow-api` (honeypot) | Fonctionnel, log d'interception prêt pour le dashboard/SIEM |
| `ml-detector` | Pipeline complet (fenêtre glissante, IsolationForest, autoencoder). 3 des 6 features sont des placeholders à brancher |
| `remediation-engine` | Fonctionnel, appelle Ansible. Le lock empêche un double déclenchement |
| `remediation_playbook.yml` | Les 4 étapes sont écrites avec `ignore_errors` partout — retirez-les au fur et à mesure que chaque endpoint admin existe vraiment |
| `front-dashboard` | 4 quadrants aux couleurs imposées (#261E48 / #E4E6F3 / #A1A6D4), données à brancher |
| `ring_signer_hsm.ino` | Les 4 états LED + servo sont codés, à flasher sur l'ESP32 |
| WAF, SIEM, RAG Ollama | Non inclus ici — ce sont des conteneurs/outils existants à configurer, pas du code à écrire from scratch |

## Priorités si le temps manque

- **P0 (ne jamais sacrifier)** : la chaîne complète attaque → `ml-detector`
  → `remediation-engine` → Ansible → nouvelle transaction prouvée. C'est
  l'axe le plus gros de toute la grille (25 pts nationaux) et une
  intervention humaine pendant la démo met 0/5 sur cet axe.
- **P1** : honeypot, WAF/SIEM, dashboard, dossier EBIOS/PCA-PRA/DORA.
- **P2 (cuttable sans remords)** : génération RAG en live (pré-générez le
  gros du rapport à l'avance), hardware ESP32 (gardez le dashboard
  capable de porter toute la démo seul si le HSM physique n'est pas prêt).

## Sécurité du livrable

Aucun secret en clair dans le ZIP final (règle éliminatoire) : vérifiez
`git secrets --scan` ou a minima `grep -r` vos clés avant de zipper, et
assurez-vous que `secrets/` et `.env` restent dans `.gitignore`.
