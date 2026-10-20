#!/usr/bin/env python3
"""Contrôle statique de la pile — sans Docker.

    python tools/check_compose.py

Pourquoi ce script existe : `docker compose up` n'est pas disponible sur tous
les postes (Docker Desktop + WSL2 représentent plusieurs Go, et l'environnement
de développement de ce dépôt n'en dispose pas). Or la plupart des pannes de
démarrage sont **statiques** : un fichier de configuration monté qui n'existe
pas, une variable d'environnement qui n'est jamais passée, un service qui
dépend d'un autre absent, un volume non déclaré.

Ce script vérifie exactement ces points, en 2 secondes, et échoue bruyamment.
Ce qu'il ne remplace PAS : un vrai `docker compose up`. Il réduit le nombre
d'aller-retours, il ne les supprime pas.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_yaml(path: pathlib.Path):
    try:
        import yaml
    except ImportError:
        print("PyYAML absent : pip install pyyaml")
        raise SystemExit(2)
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    def check(condition: bool, label: str, detail: str = "") -> None:
        status = "OK   " if condition else "ECHEC"
        print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
        if not condition:
            failures.append(label)

    compose_path = ROOT / "docker-compose.yml"
    if not compose_path.exists():
        print("docker-compose.yml introuvable")
        return 2
    compose = load_yaml(compose_path)
    services = compose.get("services", {})
    volumes = set(compose.get("volumes", {}) or {})
    networks = set(compose.get("networks", {}) or {})

    print(f"docker-compose.yml : {len(services)} services, "
          f"{len(volumes)} volumes, {len(networks)} réseaux")

    # 1. Chaque service construit un Dockerfile qui existe -----------------
    print("\n1. Contextes de construction")
    for name, spec in services.items():
        build = spec.get("build")
        if build is None:
            continue
        if isinstance(build, str):
            context = (ROOT / build).resolve()
            dockerfile = context / "Dockerfile"
        else:
            context = (ROOT / build.get("context", ".")).resolve()
            dockerfile = context / build.get("dockerfile", "Dockerfile")
        check(
            dockerfile.exists(),
            f"{name}: Dockerfile présent",
            str(dockerfile.relative_to(ROOT)) if dockerfile.exists() else str(dockerfile),
        )

    # 2. Chaque montage lié pointe sur un fichier/dossier réel ------------
    print("\n2. Montages de configuration")
    for name, spec in services.items():
        for mount in spec.get("volumes", []) or []:
            if not isinstance(mount, str) or ":" not in mount:
                continue
            source = mount.split(":", 1)[0]
            if not source.startswith("."):
                continue  # volume nommé ou chemin absolu : pas notre affaire
            path = (ROOT / source).resolve()
            check(
                path.exists(),
                f"{name}: source de montage existe",
                source,
            )

    # 3. Chaque volume nommé utilisé est déclaré --------------------------
    print("\n3. Volumes nommés")
    for name, spec in services.items():
        for mount in spec.get("volumes", []) or []:
            if not isinstance(mount, str) or ":" not in mount:
                continue
            source = mount.split(":", 1)[0]
            if source.startswith((".", "/", "~")):
                continue
            check(source in volumes, f"{name}: volume « {source} » déclaré")

    # 4. Chaque dépendance désigne un service existant --------------------
    print("\n4. Dépendances")
    for name, spec in services.items():
        depends = spec.get("depends_on") or {}
        names = depends.keys() if isinstance(depends, dict) else depends
        for dependency in names:
            check(
                dependency in services,
                f"{name}: dépend de « {dependency} » qui existe",
            )

    # 5. Réseau commun ----------------------------------------------------
    print("\n5. Réseaux")
    for name, spec in services.items():
        for network in spec.get("networks", []) or []:
            check(network in networks, f"{name}: réseau « {network} » déclaré")

    # 6. Ports uniques ----------------------------------------------------
    print("\n6. Ports publiés")
    seen: dict[str, str] = {}
    for name, spec in services.items():
        for mapping in spec.get("ports", []) or []:
            host_port = str(mapping).split(":")[0]
            if host_port in seen:
                check(False, f"{name}: port {host_port} déjà pris par {seen[host_port]}")
            else:
                seen[host_port] = name
    print(f"  {len(seen)} ports distincts : " + ", ".join(sorted(seen, key=int)))

    # 7. URLs internes : chaque hôte cité est un service connu ------------
    print("\n7. URLs inter-services")
    url_re = re.compile(r"https?://([a-z0-9-]+):(\d+)")
    for name, spec in services.items():
        for key, value in (spec.get("environment") or {}).items():
            if not isinstance(value, str):
                continue
            for host, port in url_re.findall(value):
                if host in ("localhost", "127.0.0.1", "host.docker.internal"):
                    continue
                check(host in services, f"{name}.{key}: hôte « {host} » est un service")
                if host in services:
                    ports = [str(p).split(":")[0] for p in services[host].get("ports", []) or []]
                    if ports and port not in ports:
                        warnings.append(
                            f"{name}.{key}: {host}:{port} mais {host} publie {ports}"
                        )

    # 8. Variables d'environnement du .env.example ------------------------
    print("\n8. Variables d'environnement")
    env_example = ROOT / ".env.example"
    declared: set[str] = set()
    if env_example.exists():
        for line in env_example.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                declared.add(line.split("=", 1)[0])
    used: set[str] = set()
    for spec in services.values():
        for value in (spec.get("environment") or {}).values():
            if isinstance(value, str):
                used.update(re.findall(r"\$\{([A-Z0-9_]+)", value))
    missing = sorted(used - declared)
    check(not missing, ".env.example documente toutes les variables", ", ".join(missing))
    print(f"  {len(used)} variables utilisées, {len(declared)} documentées")

    # 9. Fichiers de configuration d'observabilité ------------------------
    print("\n9. Observabilité")
    for name in ("prometheus.yml", "alert.rules.yml", "alertmanager.yml",
                 "loki.yml", "loki-rules.yml", "promtail.yml"):
        path = ROOT / "observability" / name
        check(path.exists(), f"observability/{name}")
        if path.exists() and path.suffix in (".yml", ".yaml"):
            try:
                load_yaml(path)
            except Exception as exc:  # noqa: BLE001
                check(False, f"observability/{name} : YAML invalide", str(exc))

    # 10. Cohérence des alertes actionnables ------------------------------
    print("\n10. Chaîne d'alerte → remédiation")
    engine = ROOT / "services" / "remediation-engine" / "main.py"
    rules = ROOT / "observability" / "alert.rules.yml"
    loki_rules = ROOT / "observability" / "loki-rules.yml"
    if engine.exists() and rules.exists():
        source = engine.read_text(encoding="utf-8")
        block = re.search(r"ACTIONABLE_ALERTS\s*=\s*\{(.*?)\n\}", source, re.S)
        actionable = set(re.findall(r'"([A-Za-z]+)":', block.group(1))) if block else set()
        declared_alerts = set()
        for path in (rules, loki_rules):
            if not path.exists():
                continue
            data = load_yaml(path)
            for group in data.get("groups", []):
                for rule in group.get("rules", []):
                    if "alert" in rule:
                        declared_alerts.add(rule["alert"])
        print(f"  {len(declared_alerts)} alertes déclarées, "
              f"{len(actionable)} actionnables")
        unknown = sorted(actionable - declared_alerts)
        check(not unknown, "toute alerte actionnable existe dans une règle", ", ".join(unknown))

    # Récapitulatif --------------------------------------------------------
    print()
    if warnings:
        print("Avertissements :")
        for warning in warnings:
            print(f"  - {warning}")
    if failures:
        print(f"\n{len(failures)} contrôle(s) en échec.")
        return 1
    print("Pile cohérente. (Ce contrôle est statique : `docker compose up` reste la preuve.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
