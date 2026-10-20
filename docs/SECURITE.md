# Sécurité — ce qui est fermé, comment, et comment le vérifier

> Nouvelles règles du jury : « les DEV doivent créer un SI invulnérable, hors de
> question de créer des failles volontaires. Les CYBER doivent mettre en place
> des attaques et essayer de faire tomber le SI. »
>
> Ce document n'affirme pas que le SI est invulnérable — aucun SI ne l'est. Il
> liste **ce qui a été trouvé, corrigé, et comment on le prouve**, puis ce qui
> reste ouvert. Une faille écrite vaut mieux qu'une faille découverte en Q&A.

**Résumé au 20/10 au soir.** **12 failles** fermées (F1–F12), **4 bugs
critiques** fermés (C1, C2, C4, S1), **10 limites** écrites (8 ouvertes,
2 fermées), **12 points moyens** suivis dans `TODO.md` §0.6. **219 tests** :
195 pytest, 5 tests de script, 9 tests Rust, 10 tests HTTP. Les mesures Docker
ne sont pas refaites sur ce poste (Docker absent) : elles viennent de la pile de
démonstration, et c'est dit à chaque fois.

---

## 1. Failles trouvées et fermées

Ces **douze** points n'étaient pas des « failles de démonstration » : c'étaient
des trous qu'un jury CYBER exploite en quelques minutes, avec `curl`.

| # | Faille | Exploit avant correction | Correction | Test |
|---|---|---|---|---|
| F1 | `POST /auth/login` acceptait **n'importe quel** `api_secret` | un JWT valide pour tout identifiant connu → accès aux terminaux | table `MERCHANT_SECRETS` (SHA-256), comparaison à temps constant, **503 fail-closed** si non configuré, 10 essais/min par IP | `test_mauvais_secret_refuse`, `test_sans_table_de_secrets_le_service_refuse_de_signer` |
| F2 | `/admin/*` de `payment-api` et du WAF **ouverts** | quarantaine de 25 outputs, anneau à 64, déception désarmée en une requête | `SERVICE_TOKEN` obligatoire (`svc_auth.py`), comparaison à temps constant, fail-closed 503 | `test_les_endpoints_admin_sont_fermes_sans_jeton` (payment-api ; même module `svc_auth.py` côté WAF, vérifié par la recette §4) |
| F3 | `/incident` et `/state/report` **ouverts** | playbook déclenché par n'importe qui → déni de service, faux `RECOVERED_TX` | idem F2 ; le détecteur et le playbook envoient le jeton | `test_le_siem_exige_le_jeton_de_service` |
| F4 | **PIN `1234` codé en dur** | terminal ouvert avec une valeur publique, affichée dans l'UI | `TERMINAL_PIN` généré par `tools/init_env.py`, blocage 60 s après 5 refus, l'indice disparaît de l'interface | `test_pin_refuse_compte_et_refuse`, `test_le_pin_par_defaut_ne_fonctionne_plus` |
| F5 | **Anneau non borné** sur `/tx/submit` | 100 000 leurres = saturation CPU (vérification linéaire en N) | plafond à 64 leurres appliqué par Pydantic (`max_length=64`), donc **422** avant le handler et avant toute recherche ; le handler garde un 400 en défense en profondeur | `test_anneau_trop_grand_refuse`, `test_65_leurres_sont_refuses_avant_toute_recherche` |
| F6 | `/ingest` du détecteur **ouvert** | **évasion** : diluer la fenêtre avec de fausses observations normales pour empêcher la détection ; ou l'inverse, déclencher un playbook à volonté | `SERVICE_TOKEN` obligatoire | `test_ingest_exige_le_jeton_de_service` |
| F7 | **Entrées non strictes** sur la boutique | une quantité `"3"` (chaîne) ou `2.5` était convertie silencieusement ; un SKU arbitraire (`X'; DROP TABLE …`) était stocké | `strict=True` sur les entiers, motif `^[A-Z0-9][A-Z0-9-]{1,31}$` sur le SKU → 422 | `test_une_quantite_en_chaine_est_refusee`, `test_un_sku_malforme_est_refuse` |
| F8 | **Double dépense par concurrence** (`/tx/submit`) | deux requêtes simultanées avec la même key image passaient toutes les deux le test, puis répondaient `200 confirmed` — la propriété centrale du projet était contournable par deux `curl` en parallèle | la key image est **réservée avant le premier `await`** (`asyncio.Lock` + `_pending_key_images`) ; un second appel reçoit 409 sans même payer le coût cryptographique | `test_une_double_depense_concurrente_est_refusee` |
| F9 | **Double débit par double-clic** (`/checkout`) | deux `POST /checkout` sur le même panier créaient deux commandes et deux paiements ; le `WHERE stock >= ?` empêchait la survente, pas le double débit | réclamation **atomique** du panier (`UPDATE carts SET checked_out_at = ? WHERE checked_out_at IS NULL`, rowcount vérifié) ; relâchée si le paiement échoue | `test_deux_checkouts_sur_le_meme_panier` |
| F10 | **500 + oracle d'existence** (`/orders/{ref}`) | un `customer_ref` non-ASCII levait `TypeError` dans `compare_digest` : 500 si la commande existait, 404 sinon — donc un oracle, sur des références `CMD-` + 8 hex énumérables | format validé **avant** la comparaison, puis comparaison en **octets** | `test_un_token_non_ascii_ne_fuit_pas_lexistence` |
| F11 | **Angle mort du verrouillage du PIN** | `{"pin": "é"}` provoquait un 500 **sans** incrémenter le compteur d'échecs ni le verrouillage | comparaison en octets ; le refus est compté comme les autres | `test_le_pin_non_ascii_est_compte_comme_un_echec` |
| F12 | **Alerte SIEM morte** | la règle Loki `PaiementRefuse` ciblait `{service="payment-api"}`, or la ligne est écrite par **shop-api** : l'alerte ne pouvait jamais se déclencher, donc aucune remédiation | sélecteur corrigé ; la règle sur le jeton cible le 503 réellement journalisé | `tools/check_compose.py` (contrôle 10 : chaîne alerte → actionnable) |

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

## 2 bis. Bugs de correction fermés par l'audit de code (hors critiques)

Ce ne sont **pas** des failles de sécurité : ce sont des défauts qui cassent le
service ou faussent une mesure. Ils sont listés ici parce qu'un jury demande
« qu'est-ce qui ne marchait pas ? », et parce qu'un bug non écrit revient.

| # | Bug | Effet réel | Correction | Test |
|---|---|---|---|---|
| C3 | **Outputs restaurés non dépensables** | le portefeuille était retiré au hasard à chaque démarrage ; les outputs restaurés appartenaient aux anciennes clés, donc `/tx/submit` répondait 409 une fois sur deux — exactement le symptôme que la persistance devait supprimer | portefeuille persisté dans PostgreSQL (`wallet`), rechargé **avant** toute émission | `test_le_portefeuille_est_persiste_et_recharge` |
| R2 | **Key image écrite en deux temps** | `UPDATE outputs SET spent` puis `INSERT key_images` séparés : si le processus mourait entre les deux, un rejeu passait au redémarrage | une transaction PostgreSQL | `test_la_depense_est_atomique` |
| R3 | **Restauration partielle** | `_restore_registry` retournait tôt si `outputs` était vide, donc les key images n'étaient pas restaurées | chaque partie restaurée indépendamment | `test_la_restauration_conserve_les_key_images_sans_outputs` |
| E1 | **État bloqué en `REMEDIATING`** | une `PermissionError` du playbook tuait la tâche de fond : aucun `REMEDIATION_FAILED`, dashboard orange, `/state/report` refusait `NORMAL_TX` indéfiniment | `_run_incident` en `try/except/finally` publie toujours un état terminal ; `_run_playbook` capture `OSError`/`SubprocessError` | `test_une_erreur_du_playbook_publie_un_etat_terminal` |
| E2 | **Verrou de remédiation non atomique** | `if _lock.locked()` puis `create_task` : la tâche n'avait pas encore pris le verrou, donc deux alertes actionnables lançaient deux playbooks | drapeau `_busy` positionné **synchroniquement** avant `create_task` | `test_deux_alertes_du_meme_payload_ne_lancent_quun_playbook` |
| E3 | **Webhook cassé invisible** | un 401/503 du moteur n'est pas une exception `httpx` : il passait pour un succès, `last_webhook_error` restait `None`, l'alerte ne se déclenchait pas | contrôle explicite du statut | `test_detector.py` |
| E4 | **Télémétrie jetée en silence** | idem côté `payment-api` : `/ingest` en 503 et personne ne le savait | compteurs `sent`/`dropped`/`last_error` publiés dans `/healthz` et `shadow_telemetry_dropped_total` | `test_payment_api.py` |
| E5 | **`/healthz` et `/metrics` tombaient avec la base** | le `try` ne couvrait que la première requête : base arrêtée → 500, healthcheck Docker en boucle, scrape Prometheus en échec **pendant la panne** | tout le corps protégé, `status: degraded` et `shop_database_up 0` | `test_shop_api.py` |
| L1 | **Lignes de tableau éliminées du RAG** | une ligne transformée en phrase n'est plus une sous-chaîne du Markdown : le rang était recalculé par sous-chaîne, échouait, et la ligne était rejetée dès `TOP_K=4` | le rang est porté par le candidat dès sa construction | `test_un_tableau_seul_repond_a_une_question_de_duree` |
| L2 | **Disjoncteur Ollama non thread-safe** | N requêtes simultanées lisaient toutes le disjoncteur fermé avant qu'une ne le tripé : N appels bloquants, threadpool saturé, healthcheck Docker en échec | verrou + **single-flight** : un seul thread sonde, les autres répondent en extractif | `test_le_disjoncteur_ne_laisse_passer_quune_sonde` |

## 2 ter. Les quatre bugs critiques de l'audit de code

L'audit de code a classé quatre défauts **critiques** : ils cassaient le service
ou annulaient une propriété de sécurité. Tous sont fermés, chacun avec son test
de régression. Trois recoupent des failles du §1 — c'est le même défaut vu deux
fois (lecture « faille exploitable » en §1, lecture « bug » ici), et le test est
le même.

| # | Bug critique | Ce qui se passait | Correction | Test de régression |
|---|---|---|---|---|
| C1 | **Boutique inutilisable sur PostgreSQL** | le schéma déclare `active BOOLEAN`, le SQL écrivait `active = 1`. psycopg encode `1` en `int4` ; PostgreSQL n'a aucun cast `integer → boolean`. L'exception survenait dans `_seed_catalog`, donc dans `lifespan`, **avant** le `yield` : uvicorn « Application startup failed », conteneur en boucle | `TRUE`/`FALSE` dans le SQL (les deux moteurs les comprennent) et `True`/`False` en Python | `test_postgres_dialect.py` (4 tests statiques) — **la preuve finale reste `docker compose up`**, non refaite sur ce poste |
| C2 | **Double dépense concurrente** (= F8) | deux `POST /tx/submit` simultanés avec la même key image passaient tous les deux le contrôle, puis répondaient `200 confirmed` | key image **réservée avant le premier `await`** (`asyncio.Lock` + `_pending_key_images`) ; le second appel reçoit 409 | `test_une_double_depense_concurrente_est_refusee` |
| C4 | **Double débit au double-clic** (= F9) | deux `POST /checkout` sur le même panier créaient deux commandes et deux paiements ; `WHERE stock >= ?` empêchait la survente, pas le double débit | réclamation **atomique** du panier (`UPDATE ... WHERE checked_out_at IS NULL`, rowcount vérifié), relâchée si le paiement échoue | `test_deux_checkouts_sur_le_meme_panier` |
| S1 | **Oracle 404 sur `/orders/{ref}`** (= F10) | un `customer_ref` non-ASCII levait `TypeError` dans `compare_digest` : 500 si la commande existait, 404 sinon — l'attaquant apprenait l'existence de la référence | format validé **avant** la comparaison, comparaison en **octets**, **404 uniforme** | `test_un_token_non_ascii_ne_fuit_pas_lexistence` |

**Les 12 points moyens** relevés par le même audit ne sont pas cachés : ils sont
listés dans `TODO.md` §0.6 avec un critère d'acceptation. Plusieurs ont été
fermés le 20/10 au soir (§6) ; ceux qui restent sont réels.

## 3. Ce qui reste ouvert (assumé, écrit)

| # | Point | Risque | Pourquoi c'est acceptable | Quand |
|---|---|---|---|---|
| O1 | **Pas de TLS** | un attaquant sur le réseau peut lire le trafic | la démonstration est sur `localhost` ; la caméra l'exige (`getUserMedia`) | production : TLS + HSTS |
| O2 | **Pas de détection de vivacité** faciale | une photo imprimée passe le facteur visage | le mot de passe reste exigé ; publié dans `/healthz` | chantier dédié |
| O3 | **Pas de Bulletproofs** | un montant négatif modulo l passe l'équilibre | nécessite un attaquant authentifié qui forge ses engagements ; publié dans `/healthz` | `TODO.md` §1.1 |
| O4 | ~~Registre d'outputs en mémoire~~ — **fermé** | — | journal PostgreSQL (`registry.py`, 16 tests) | — |
| O5 | **Blocage 5 min exploitable** en déni de service ciblé | un tiers peut bloquer un compte connu | imposé par la consigne ; déblocage admin + alerte `ComptesBloques` | assumé |
| O6 | **Pas de WAF générique** (ModSecurity/CRS) | pas de couverture OWASP top-10 générique | les entrées sont validées par Pydantic dans chaque service ; le WAF maison fait de la déception, pas du filtrage | assumé |
| O7 | **Pas de revue d'accès périodique** | un compte oublié reste actif | journal d'audit + sessions révocables | gouvernance |
| O8 | ~~Reconnexion du registre non automatique~~ — **fermé le 20/10** | après une coupure PostgreSQL, les écritures étaient perdues en silence | reconnexion avec backoff 1 → 30 s, `lost_writes` et `reconnect_attempts` publiés dans `/healthz` ; `test_la_reconnexion_restaure_available_et_compte_les_pertes` | — |
| O9 | **RAG `/ask` sans authentification ni limite de débit** | toute personne joignant le port 8091 interroge le dossier technique et peut en extraire des passages ; le contexte BM25 est injecté tel quel dans le prompt Ollama, donc un document modifié dans `docs/` ou une question formulée comme une instruction peut détourner la génération | les documents indexés sont **déjà versionnés dans le dépôt** : la fuite se limite à ce qui est public. En mode extractif, le service cite des passages, il n'obéit pas. Acceptable sur réseau local de démonstration | production : jeton, quota par IP, délimitation stricte des extraits |
| O10 | **`/history` du moteur est public en lecture** | un attaquant apprend l'état de la remédiation, les diagnostics, le code retour Ansible et les 2 000 derniers caractères de sortie du playbook | **choix assumé** : le dashboard doit l'afficher sans jeton (il consomme aussi `/state` et `/events`). Le contenu est une trace de playbook, pas un secret : la sortie connue affiche des compteurs (diagnostic, quarantaine, `ring_size`), pas de clé ; à revérifier si le playbook change | production : lecture derrière la session back-office |

---

## 4. Comment vérifier (recette CYBER)

```bash
# 1. aucun secret dans le dépôt (règle éliminatoire)
git ls-files | xargs grep -l "hvs\.\|BEGIN.*PRIVATE KEY" 2>/dev/null   # doit être vide

# 2. les endpoints d'administration sont fermés sans jeton
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8080/admin/quarantine-outputs   # 401 ou 503
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8085/admin/reset                # 401 ou 503
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8084/incident -d '{}'           # 401 ou 503
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8084/alert \
  -H 'Content-Type: application/json' -d '{"status":"firing","alerts":[]}'                  # 401 ou 503
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
print(r.status_code)  # 422 « anneau trop grand » (validation Pydantic)
PY

# 6. l'historique de la boutique est fermé sans capability token
curl -s -o /dev/null -w "%{http_code}\n" localhost:8087/orders/CMD-0001                     # 404
```

### Résultat mesuré des attaques sur la boutique

```
python tools/attack_shop.py     # 6 familles, ~1 s

quantités invalides      5/5 refusées (422)
panier forgé             4/4 sans contenu : 3 × 404, 1 × 307 de normalisation
                         (jeton vide → redirection `/cart/` ; ce n'est pas un
                         refus, mais aucun panier n'est rendu)
injection SQL par SKU    3/3 refusées (422), catalogue intact
historique d'autrui      3/3 détails en 404 ; liste avec jeton inventé :
                         200 et 0 commande retournée
course sur le stock      stock jamais négatif, aucune survente
falsification de prix    montant facturé = prix en base, champ ignoré
service                 toujours vivant
```

Ce n'est pas une preuve d'invulnérabilité — c'est la preuve que **ces six
attaques-là** échouent, et qu'elles sont rejouables en une commande.

Les tests automatisés couvrent les mêmes points : `services/auth-service`,
`services/waf`, `services/payment-api`, `services/shadow-api`,
`services/remediation-engine`, `services/backoffice`, `services/shop-api`.

---

## 5. Attaques que le SI doit encaisser (rôle CYBER)

| Attaque | Attendu | Où c'est testé |
|---|---|---|
| Quantité négative / absurde / en chaîne | **422** | `tools/attack_shop.py` |
| Panier forgé, jeton inventé | **404** (ou **307** de normalisation pour un jeton vide), jamais de contenu | `tools/attack_shop.py` |
| Injection SQL par le SKU | **422**, catalogue intact | `tools/attack_shop.py` |
| Énumération de l'historique d'autrui | **404** sur le détail, **0 commande** sur la liste | `tools/attack_shop.py` |
| Course sur le dernier article | une commande, stock jamais négatif | `tools/attack_shop.py`, `test_achats_simultanes_ne_survendent_pas` |
| Prix annoncé par le client | ignoré, total = base | `tools/attack_shop.py` |
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

---

## 6. Ce qui a été durci le 20/10 au soir

Ces huit mesures répondent à des trous trouvés après l'audit initial. Elles sont
listées avec leur test, parce qu'une correction sans test de régression revient
au premier refactor.

| # | Durcissement | Ce qui change | Preuve |
|---|---|---|---|
| 1 | **Corrélation multi-alertes** | une panne réelle produit 3 à 5 alertes : elles sont rattachées au **même** incident dans la fenêtre de corrélation et republiées dans `detail.alerts` ; hors fenêtre, elles sont ignorées avec `reason: "hors_fenetre_de_correlation"` au lieu d'ouvrir un second playbook | `test_une_alerte_tardive_est_rattachee_a_lincident_courant`, `test_une_rafale_dalertes_produit_un_seul_incident`, `test_une_alerte_hors_fenetre_est_ignoree` |
| 2 | **`/tx/pay` idempotent par `reference`** | un rejeu (timeout HTTP, retry réseau) renvoie le **même** résultat avec `replayed: true` au lieu d'émettre un second paiement ; une référence réutilisée avec un autre montant reçoit **409**. Verrou par référence, pas verrou global | `test_deux_appels_avec_la_meme_reference_ne_paient_qu_une_fois`, `test_deux_rejeux_simultanes_ne_paient_qu_une_fois`, `test_un_rejeu_apres_perte_de_la_memoire_est_rejoue_depuis_le_journal`, `test_une_reference_reutilisee_avec_un_autre_montant_est_refusee` |
| 3 | **Rate limit optionnel `TX_PAY_RATE_LIMIT`** | fenêtre glissante par IP, **429** + `Retry-After` ; **désactivé par défaut** (`0`) pour ne pas fausser `tools/load_test.py` | `test_tx_pay_est_rate_limite_par_ip`, `test_tx_pay_rate_limit_est_desactive_par_defaut` |
| 4 | **Reconnexion du registre** | après une coupure PostgreSQL, backoff exponentiel 1 → 30 s dans un thread dédié (jamais dans le chemin du paiement) ; `/healthz` publie `lost_writes` et `reconnect_attempts`, les métriques aussi | `test_la_reconnexion_restaure_available_et_compte_les_pertes`, `test_une_reconnexion_ratee_ne_leve_pas_et_recule` |
| 5 | **Balayage des commandes `pending`** | TTL de réservation (120 s par défaut), balayage au démarrage puis toutes les 30 s ; réclamation atomique, restitution du stock, réconciliation par `reference` pour ne pas rembourser deux fois un paiement réussi | `test_le_balayage_au_demarrage_repare_une_commande_orpheline`, `test_le_balayage_expire_une_commande_pending_et_restitue_le_stock`, `test_le_balayage_ne_touche_pas_une_commande_recente`, `test_la_reconciliation_ne_restitue_pas_deux_fois_un_paiement_reussi`, `test_le_ttl_de_reservation_est_configurable` |
| 6 | **Garde « dernier administrateur » atomique** | comptage et écriture dans la **même transaction** (`BEGIN IMMEDIATE`) ; seuls les admins **avec visage** comptent, puisqu'un admin sans visage ne peut pas se connecter | `test_deux_patch_concurrents_un_seul_admin_connectable_survit`, `test_le_dernier_admin_avec_visage_ne_peut_pas_etre_desactive`, `test_le_visage_du_dernier_admin_connectable_ne_peut_pas_etre_retire`, `test_un_admin_sans_visage_ne_compte_pas_dans_la_garde` |
| 7 | **Images produit servies par `StaticFiles`** | un dossier **fixe** est monté (`services/shop-api/static`) ; Starlette résout le chemin demandé **à l'intérieur** de ce dossier (pas de `..` possible), donc pas de path traversal ; dossier absent → 404, pas 500 (`FailSoftStaticFiles`) | `test_les_images_des_six_articles_sont_servies`, `test_le_catalogue_expose_une_image_pour_chaque_article`, `test_le_montage_statique_survit_a_un_dossier_absent` |
| 8 | **`decoy_ids` borné avant tout traitement** | la longueur est bornée par Pydantic (`max_length=64`) **avant** le handler : 65 leurres → **422** immédiat, sans parcourir le registre pour retrouver les inconnus | `test_65_leurres_sont_refuses_avant_toute_recherche`, `test_anneau_trop_grand_refuse` |

**Ce que ces huit lignes ne règlent pas.** Elles ferment une partie des **12
points moyens** relevés par l'audit ; le reste est suivi ligne par ligne dans
`TODO.md` §0.6, avec un critère d'acceptation. Rien n'est retiré de cette liste
parce qu'il aurait été « presque » corrigé.

**Une collision de schéma, trouvée puis fermée.** `payment-api` et `shop-api`
créaient tous les deux une table `payments` dans la **même** base
`shadow_settle` (colonnes différentes : `reference/key_image/...` d'un côté,
`order_id/provider/...` de l'autre). Avec `CREATE TABLE IF NOT EXISTS`, le
premier service démarré imposait son schéma et l'autre échouait à l'insertion
(« column does not exist ») — l'achat aurait été cassé sous Docker, alors que
les tests unitaires, eux, passaient (chaque service teste sa propre base).
Le registre écrit désormais dans **`registry_payments`**, et
`tools/check_compose.py` refuse toute table homonyme entre deux services
(contrôle statique, joué par `run_tests.ps1`). La correction n'a pas été
rejouée sous `docker compose up` sur ce poste : c'est la première vérification
du 21/10.

---

## 7. Dépendances — `pip-audit`, et ce qui reste

```bash
pip-audit -r services/payment-api/requirements.txt \
          -r services/shop-api/requirements.txt \
          -r services/rag-assistant/requirements.txt \
          -r services/ml-detector/requirements.txt \
          -r services/backoffice/requirements.txt \
          -r services/auth-service/requirements.txt \
          -r services/waf/requirements.txt \
          -r services/shadow-api/requirements.txt \
          -r services/remediation-engine/requirements.txt
```

**Ce que le scan a trouvé, et ce qui a été corrigé.** Les `requirements.txt`
figeaient `fastapi==0.115.0`, qui installe **starlette 0.38.6** — touchée par
sept avis PYSEC (multipart non borné, `Host` non validé, `StaticFiles` sur
Windows). Les tests, eux, tournaient sur `fastapi 0.152` / `starlette 1.9` :
l'image n'était pas ce que la suite validait. Les versions ont été alignées sur
celles réellement testées (`fastapi 0.152.0`, `starlette 1.9.0`, `pydantic
2.14.1`, `numpy 2.5.4`, `scikit-learn 1.9.1`, `torch 2.14.1`), et
`python-jose` est passé de 3.3.0 à **3.4.0** (PYSEC-2024-232/233,
PYSEC-2025-185).

**Ce qui reste, et pourquoi c'est justifié par écrit.** Le scan remonte encore
deux dépendances **transitives** de `python-jose`, non atteignables dans ce SI :

| Paquet | Avis | Pourquoi il n'est pas atteignable |
|---|---|---|
| `pyasn1` 0.4.8 | PYSEC-2026-2263/3455/3456/3457 (DoS par décodage ASN.1) | `python-jose` ne l'importe que pour le backend **RSA**. L'algorithme est `HS256` (`JWT_ALGO` dans `services/auth-service/main.py`) : aucun chemin ne décode de l'ASN.1 fourni par un client. |
| `ecdsa` 0.19.2 | PYSEC-2026-1325 (Minerva, courbe P-256) | Le backend **ECDSA** n'est jamais utilisé (HS256). Aucune signature de courbe dans le SI. |

`python-jose` lui-même porte **PYSEC-2026-4298** (confusion de clé : une clé
publique DER acceptée comme secret HMAC). Il n'y a **pas de correctif** publié.
La condition d'exploitation est l'existence d'une clé **asymétrique** dont la
partie publique serait connue de l'attaquant : ici le secret HMAC est une valeur
aléatoire lue dans Vault, jamais distribuée, et aucun chemin asymétrique
n'existe. C'est une limite écrite, pas une faille ouverte.

**Décision assumée** : on ne remplace pas `python-jose` par `PyJWT` trois jours
avant la soutenance. Le remplacement est la première tâche après le workshop
(`TODO.md` §0.6.6).
