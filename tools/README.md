# Outils de recette

Ces scripts servent à **prouver** que le système fonctionne, pas à le faire
fonctionner. Aucun n'est appelé par les services en production.

| Script | Rôle |
|---|---|
| `e2e_demo.py` | Répétition générale : attaque → détection → remédiation → preuve, chronométrée |
| `fake_ansible.py` | Stub de `ansible-playbook` pour rejouer la chaîne sans Ansible installé |

## Répétition générale

```bash
python tools/e2e_demo.py
```

Sortie attendue :

```json
{
  "detection_seconds": 10.1,
  "recovery_seconds": 12.6,
  "total_seconds": 35.9,
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

## Rejouer sans Ansible

Utile sur un poste où seul Docker n'est pas encore disponible :

```bash
# service remediation-engine lancé avec :
ANSIBLE_BIN=python
ANSIBLE_PLAYBOOK=/chemin/vers/tools/fake_ansible.py
```

Le stub imite la sortie réelle du playbook, y compris la ligne `probe_ok=True`
que `remediation-engine` lit pour décider de l'état final. Il ne remplace
évidemment pas une validation avec le vrai playbook.
