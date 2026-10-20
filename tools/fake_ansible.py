"""Stub de recette : simule `ansible-playbook` quand Ansible n'est pas installé.

    ANSIBLE_BIN=python ANSIBLE_PLAYBOOK=tools/fake_ansible.py

Ne fait PAS partie de la chaîne de production. Il sert uniquement à rejouer la
boucle complète (détection -> remédiation -> reprise) sur un poste sans Ansible,
en attendant le conteneur. Il imite la sortie réelle du playbook, y compris la
ligne `probe_ok=` que remediation-engine lit pour décider de l'état final.
"""
import sys
import time

DURATION = 2.0  # un playbook réel prend 4 à 8 s ; 2 s suffisent en recette

print("PLAY [Remédiation autonome SHADOW-SETTLE] *************************************")
print()
for task in (
    "Récupérer un token Vault à portée restreinte (AppRole)",
    "Basculer le WAF vers le honeypot",
    "Mettre en quarantaine les outputs empoisonnés",
    "Durcir dynamiquement la taille des anneaux",
    "Faire tourner le secret de signature JWT",
    "Relancer le worker payment-api",
    "Vérifier qu'une nouvelle transaction légitime passe",
):
    time.sleep(DURATION / 7)
    print(f"TASK [{task}] ***")
    print("changed: [localhost]")
    print()

print("PLAY RECAP *********************************************************************")
print("localhost                  : ok=7    changed=6    unreachable=0    failed=0")
print()
print("probe_ok=True")
sys.exit(0)
