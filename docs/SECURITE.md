# Sécurité — ce qui est fermé, comment, et comment le vérifier

> Nouvelles règles du jury : « les DEV doivent créer un SI invulnérable, hors de
> question de créer des failles volontaires. Les CYBER doivent mettre en place
> des attaques et essayer de faire tomber le SI. »
>
> Ce document n'affirme pas que le SI est invulnérable — aucun SI ne l'est. Il
> liste **ce qui a été trouvé, corrigé, et comment on le prouve**, puis ce qui
> reste ouvert. Une faille écrite vaut mieux qu'une faille découverte en Q&A.

---

## 1. Failles trouvées et fermées

Ces cinq points n'étaient pas des « failles de démonstration » : c'étaient des
trous qu'un jury CYBER exploite en quelques minutes, avec `curl`.

| # | Faille | Exploit avant correction | Correction | Test |
|---|---|---|---|---|
| F1 | `POST /auth/login` acceptait **n'importe quel** `api_secret` | un JWT valide pour tout identifiant connu → accès aux terminaux | table `MERCHANT_SECRETS` (SHA-256), comparaison à temps constant, **503 fail-closed** si non configuré, 10 essais/min par IP | `test_mauvais_secret_refuse`, `test_sans_table_de_secrets_le_service_refuse_de_signer` |
| F2 | `/admin/*` de `payment-api` et du WAF **ouverts** | quarantaine de 25 outputs, anneau à 64, déception désarmée en une requête | `SERVICE_TOKEN` obligatoire (`svc_auth.py`), comparaison à temps constant, fail-closed 503 | `test_ladministration_exige_une_session_admin` |
| F3 | `/incident` et `/state/report` **ouverts** | playbook déclenché par n'importe qui → déni de service, faux `RECOVERED_TX` | idem F2 ; le détecteur et le playbook envoient le jeton | `test_le_siem_exige_le_jeton_de_service` |
| F4 | **PIN `1234` codé en dur** | terminal ouvert avec une valeur publique, affichée dans l'UI | `TERMINAL_PIN` généré par `tools/init_env.py`, blocage 60 s après 5 refus, l'indice disparaît de l'interface | `test_pin_*` |
| F5 | **Anneau non borné** sur `/tx/submit` | 100 000 leurres = saturation CPU (vérification linéaire en N) | plafond à 64 leurres, 400 explicite | `test_anneau_trop_grand_refuse` |
| F6 | `/ingest` du détecteur **ouvert** | **évasion** : diluer la fenêtre avec de fausses observations normales pour empêcher la détection ; ou l'inverse, déclencher un playbook à volonté | `SERVICE_TOKEN` obligatoire | `test_ingest_exige_le_jeton` |

**Pourquoi le fail-closed plutôt qu'un jeton par défaut** : un défaut connu est
une faille (tout le monde le lit dans le dépôt). Un service qui refuse de servir
tant qu'il n'est pas configuré est bruyant — et `tools/init_env.py` le configure
en une commande. L'alerte `JetonServiceNonConfigure` le signale dans Prometheus.

---

## 2. Mesures en place, par catégorie

### Authentification et sessions

| Mesure | Où |
|---|---|
| PBKDF2-HMAC-SHA256, 600 000 itérations, sel 16 octets | `services/backoffice/security.py` |
| Sessions opaques : 32 octets aléatoires, seul le SHA-256 est stocké | `services/backoffice/db.py` |
| Révocation immédiate (déconnexion, changement de mot de passe, désactivation) | `main.py` (`logout`, `PATCH`, `DELETE`) |
| 2 échecs → blocage 5 min, **compteur en base** (recharger la page ne le lève pas) | `services/backoffice/main.py` |
| Anti-énumération : identifiant inconnu, compte inactif et visage absent renvoient la même réponse | `identify`, `_verify_merchant` |
| Dernier administrateur protégé (ni désactivé ni rétrogradé) | `PATCH /admin/users/{id}` → 409 |
| Rate-limit du login marchand (10/min/IP) | `services/auth-service/main.py` |

### Autorisation

| Mesure | Où |
|---|---|
| Trois rôles (`admin`, `operator`, `auditor`), vérifiés à chaque route | `require_role` |
| Administration de la boutique : session back-office **validée auprès du back-office**, rôle admin exigé | `services/shop-api/main.py` (`require_admin`) |
| Le jeton de service n'est **jamais** envoyé au navigateur | `front-dashboard/src/shop/api.js` |
| Un compte sans visage enrôlé ne peut pas se connecter | `POST /auth/identify` |

### Données

| Mesure | Où |
|---|---|
| Requêtes paramétrées partout ; aucune concaténation de valeur | tous les `db.run` / `db.query` |
| Colonnes d'un `UPDATE` dynamique vérifiées contre une **liste blanche** | `backoffice/main.py`, `shop-api/main.py` |
| Stock réservé en transaction (`WHERE stock >= ?`), restitué si échec | `shop-api` `/checkout` |
| Historique par **capability token** 256 bits, `404` et non `403` | `GET /orders/{ref}` |
| Aucun montant accepté du client : le total vient de la base | `shop-api` `/checkout` |
| Aucune clé privée ne sort d'un service (`/wallet/public` ne renvoie que les publiques) | `test_aucune_cle_privee_exposee` |

### Transport et en-têtes

| Mesure | Où |
|---|---|
| CORS restreint aux origines du poste hôte (aucun `*`) | tous les services |
| `nosniff`, `X-Frame-Options: DENY`, `no-referrer` | `backoffice`, `shop-api` |
| `Cache-Control: no-store` sur `/auth` et `/admin` | idem |
| Corps de requête borné (64 Ko) | `shop-api` (413) |
| Anneau borné (64), quantité bornée (20), montants bornés | `payment-api`, `shop-api` |

### Secrets

| Mesure | Où |
|---|---|
| `.env` ignoré par git ; `.env.example` ne contient que des valeurs vides | `.gitignore`, `.env.example` |
| `secrets.h` du firmware ignoré | `.gitignore` |
| Tous les secrets générés aléatoirement | `tools/init_env.py` |
| Jeton Alertmanager injecté au démarrage, jamais écrit dans un fichier | `docker-compose.yml` (`sed`) |
| Aucun secret dans les logs ; le PIN généré est publié **une fois** au démarrage, jamais dans `/healthz` | `payment-api` |

---

## 3. Ce qui reste ouvert (assumé, écrit)

| # | Point | Risque | Pourquoi c'est acceptable | Quand |
|---|---|---|---|---|
| O1 | **Pas de TLS** | un attaquant sur le réseau peut lire le trafic | la démonstration est sur `localhost` ; la caméra l'exige (`getUserMedia`) | production : TLS + HSTS |
| O2 | **Pas de détection de vivacité** faciale | une photo imprimée passe le facteur visage | le mot de passe reste exigé ; publié dans `/healthz` | chantier dédié |
| O3 | **Pas de Bulletproofs** | un montant négatif modulo l passe l'équilibre | nécessite un attaquant authentifié qui forge ses engagements ; publié dans `/healthz` | `TODO.md` §1.1 |
| O4 | **Registre d'outputs en mémoire** | perdu au redémarrage | n'affecte pas l'intégrité, seulement la disponibilité | `TODO.md` §1.3 |
| O5 | **Blocage 5 min exploitable** en déni de service ciblé | un tiers peut bloquer un compte connu | imposé par la consigne ; déblocage admin + alerte `ComptesBloques` | assumé |
| O6 | **Pas de WAF générique** (ModSecurity/CRS) | pas de couverture OWASP top-10 générique | les entrées sont validées par Pydantic dans chaque service ; le WAF maison fait de la déception, pas du filtrage | assumé |
| O7 | **Pas de revue d'accès périodique** | un compte oublié reste actif | journal d'audit + sessions révocables | gouvernance |

---

## 4. Comment vérifier (recette CYBER)

```bash
# 1. aucun secret dans le dépôt (règle éliminatoire)
git ls-files | xargs grep -l "hvs\.\|BEGIN.*PRIVATE KEY" 2>/dev/null   # doit être vide

# 2. les endpoints d'administration sont fermés sans jeton
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8080/admin/quarantine-outputs   # 401 ou 503
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8085/admin/reset                # 401 ou 503
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8084/incident -d '{}'           # 401 ou 503
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8083/ingest -d '{}'             # 401 ou 503

# 3. le login marchand refuse un mauvais secret
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8082/auth/login \
  -H 'Content-Type: application/json' -d '{"merchant_id":"terminal-1","api_secret":"x"}'    # 401

# 4. le PIN par défaut ne fonctionne plus
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8080/pin/verify \
  -H 'Content-Type: application/json' -d '{"pin":"1234"}'                                   # 401

# 5. l'anneau est borné
python - <<'PY'
import httpx
ids = [f"x{i}" for i in range(200)]
r = httpx.post("http://localhost:8080/tx/submit", json={"real_output_id": "output_1", "decoy_ids": ids})
print(r.status_code)  # 400 « anneau trop grand »
PY

# 6. l'historique de la boutique est fermé sans capability token
curl -s -o /dev/null -w "%{http_code}\n" localhost:8087/orders/CMD-0001                     # 404
```

Les tests automatisés couvrent les mêmes points : `services/auth-service`,
`services/waf`, `services/payment-api`, `services/shadow-api`,
`services/remediation-engine`, `services/backoffice`, `services/shop-api`.

---

## 5. Attaques que le SI doit encaisser (rôle CYBER)

| Attaque | Attendu | Où c'est testé |
|---|---|---|
| Rejeu de key image (double dépense) | **409**, key image consommée une fois | `tools/attack.py replay` |
| Leurres empoisonnés (anneau uniforme) | détection ML, déception active | `tools/attack.py poison` |
| Rafale automatisée | détectée par la cadence | `tools/attack.py burst` |
| Entrées malformées (hex, anneau vide) | **400**, aucun panic | `tools/attack.py malformed` |
| Panier forgé / quantité négative | **422**, bornes Pydantic | `test_les_quantites_sont_bornees` |
| Achat sans stock | **409**, stock jamais négatif | `test_deux_commandes_ne_survendent_pas` |
| Historique d'autrui | **404** | `test_lhistorique_exige_le_customer_ref` |
| Administration sans session | **401** | `test_ladministration_exige_une_session_admin` |
| Jeton de service deviné | **401** (comparaison à temps constant) | `test_un_service_token_invalide_est_refuse` |
| Photo devant la caméra | **accepté** — limite O2, écrite | — |
