# Outils de recette

Ces scripts servent à **prouver** que le système fonctionne, pas à le faire
fonctionner. Aucun n'est appelé par les services en production.

| Script | Rôle |
|---|---|
| `e2e_demo.py` | Répétition générale : attaque → détection → remédiation → preuve, chronométrée |
| `preflight.py` | **Pré-vol de soutenance** : services, secrets, PIN, dépôt — dit si le SI est démontrable |
| `run_tests.ps1` | Lance toutes les suites (Python + Rust) sous Windows PowerShell, avec récapitulatif |
| `run_tests.sh` | Lance les 181 tests du dépôt (Python + Rust), avec récapitulatif |
| `check_compose.py` | Contrôle statique de `docker-compose.yml` (19 services attendus, montages, ports, alertes) |
| `fake_ansible.py` | Stub de `ansible-playbook` — **inutile depuis que l'image contient Ansible** |

## Tests

Les deux lanceurs exécutent **les mêmes suites, dans le même ordre**, chacune
dans le dossier de son service (plusieurs services exposent un `main.py` : les
lancer dans un seul processus ferait résoudre `import main` vers le mauvais
module).

```bash
# Linux / macOS, ou Git Bash
./tools/run_tests.sh          # tout
./tools/run_tests.sh --fast   # sans train.py --check
```

```powershell
# Windows PowerShell (5.1 ou 7+)
.\tools\run_tests.ps1
.\tools\run_tests.ps1 -Fast   # sans train.py --check
```

Le lanceur PowerShell utilise `.venv\Scripts\python.exe` par défaut. Pour un
autre interpréteur :

```powershell
$env:PYTHON = "C:\Python312\python.exe"
.\tools\run_tests.ps1 -Fast
```

Sous Git Bash, `run_tests.sh` bascule tout seul sur `.venv/Scripts/python.exe`
si `.venv/bin/python` n'existe pas.

Codes de sortie (identiques pour les deux) : `0` tout est vert, `1` au moins une
suite a échoué, `2` le venv est introuvable. `cargo absent : tests Rust ignorés`
s'affiche si rustup n'est pas installé, et `test_crypto_flow.py` est compté
« ignoré » (et non en échec) quand la pile n'est pas démarrée.

## Contrôle statique de la pile

Sans Docker, en deux secondes :

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe tools\check_compose.py
```

Il vérifie la **liste** des 19 services attendus (un service retiré ne fait pas
échouer `docker compose up`, mais casse la démo), les Dockerfiles, les montages,
les volumes, les dépendances, les ports, les URLs inter-services, les variables
de `.env.example` et la chaîne alerte → remédiation. Sortie forcée en UTF-8 :
plus de `UnicodeEncodeError` sur les flèches dans une console cp1252.

## Pré-vol de soutenance

À lancer **la veille** et **le matin** de la démonstration :

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe tools\preflight.py
```

```text
+----------------------------------------------+--------+--------------------------------------+
| Contrôle                                     | État   | Détail                               |
+----------------------------------------------+--------+--------------------------------------+
| secrets : SERVICE_TOKEN                      | OK     | défini                               |
| service P0 : payment-api (/healthz)          | OK     | crypto_core=ready, pin_source=env    |
| service P1 : shop-api (/healthz + /products) | AVERT. | connexion refusée — service arrêté ? |
+----------------------------------------------+--------+--------------------------------------+
```

Ce qu'il contrôle :

- `.env` présent et **secrets non vides** (`SERVICE_TOKEN`,
  `BO_BOOTSTRAP_TOKEN`, `LED_DEVICE_TOKEN`, `TERMINAL_PIN`). Les valeurs ne sont
  **jamais** affichées, seulement « défini » / « non défini » ;
- cohérence du **PIN** : défini, format attendu, et surtout réellement transmis
  au conteneur `payment-api` (un PIN absent de `docker-compose.yml` donne un PIN
  aléatoire à chaque démarrage, invisible jusqu'au moment de le taper) ;
- santé HTTP : payment (`crypto_core=ready`), ml (`model=ready`,
  `last_webhook_error=null`), remediation, waf, prometheus, alertmanager,
  grafana, backoffice, shop (`>= 6` articles illustrés), rag, loki, shadow ;
- dépôt livrable : `git status` propre (avertissement) et aucun secret suivi par
  git (bloquant).

Codes de sortie : `1` si un contrôle **bloquant** échoue (services P0 ou
secrets), `0` sinon — un service non-P0 absent reste un avertissement. Pour une
sortie machine :

```powershell
.\.venv\Scripts\python.exe tools\preflight.py --json
```

`--timeout 1.5` réduit le délai par sonde (3 s par défaut).

## Répétition générale

```bash
python tools/e2e_demo.py
```

Sortie attendue :

```json
{
  "detection_seconds": 2.3,
  "recovery_seconds": 6.3,
  "total_seconds": 16.0,
  "limit_seconds": 90.0,
  "proof_transaction_ok": true,
  "ansible_rc": 0,
  "chain_complete": true
}
```

Le script échoue explicitement (code 1) si :

- un service est injoignable ;
- le modèle ML n'est pas chargé ;
- le webhook de remédiation est cassé (le détecteur expose son erreur) ;
- une anomalie est détectée **avant** l'attaque (modèle mal calibré) ;
- la fenêtre glissante ne se remplit pas (le test ne prouverait rien) ;
- la chaîne dépasse 90 s.

Il attend aussi que la fenêtre du détecteur soit **vide** avant de mesurer
(`window_size == 0`) : deux exécutions consécutives donnent donc le même
résultat, au lieu de voir le reste de l'attaque précédente.

## Rejouer sans Ansible

Utile sur un poste où Docker n'est pas disponible (Ansible est désormais dans
l'image `remediation-engine`, ce stub n'est plus nécessaire) :

```bash
# service remediation-engine lancé avec :
ANSIBLE_BIN=python
ANSIBLE_PLAYBOOK=/chemin/vers/tools/fake_ansible.py
```

Le stub imite la sortie réelle du playbook, y compris la ligne `probe_ok=True`
que `remediation-engine` lit pour décider de l'état final. Il ne remplace
évidemment pas une validation avec le vrai playbook.
