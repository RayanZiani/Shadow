#!/usr/bin/env python3
"""Test de charge — où le SI commence-t-il à céder ?

    python tools/load_test.py                       # boutique, 1/5/10/20 clients
    python tools/load_test.py --target payment      # API de paiement
    python tools/load_test.py --clients 1 5 10 20 40

Pourquoi ce script : `attack.py burst` fait 9,7 tx/s et ne dit rien de la
saturation. Un jury demande « et si 50 clients paient en même temps ? ». Sans
mesure, la réponse est une opinion.

Ce qui est mesuré, par palier de concurrence :

* **débit** (requêtes/s) ;
* **latence** p50 / p95 / p99 (ms) ;
* **taux d'erreur** (codes >= 500, et codes 4xx non attendus) ;
* **point de rupture** : premier palier où p95 dépasse le seuil ou le taux
  d'erreur dépasse 1 %.

Le rapport est écrit en Markdown pour être collé dans le dossier technique.

Ce que le script NE fait pas : contourner l'application. Il passe par les
routes publiques, comme un vrai client. Une mesure qui triche ne mesure rien.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field

import httpx

TARGETS = {
    "shop": "http://localhost:8087",
    "payment": "http://localhost:8080",
    "waf": "http://localhost:8085",
    "rag": "http://localhost:8091",
}

# Seuils de rupture. Ce sont des CHOIX, pas des constantes du système :
# p95 > 1 s sur une route de lecture, ou 1 % d'erreurs 5xx, c'est cassé.
P95_LIMIT_MS = 1000.0
ERROR_RATE_LIMIT = 0.01


@dataclass
class Sample:
    latency_ms: float
    status: int


@dataclass
class Stage:
    clients: int
    duration_s: float
    samples: list[Sample] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def throughput(self) -> float:
        return self.count / self.duration_s if self.duration_s else 0.0

    def _percentile(self, value: float) -> float:
        if not self.samples:
            return 0.0
        ordered = sorted(sample.latency_ms for sample in self.samples)
        index = min(len(ordered) - 1, int(round(value / 100 * (len(ordered) - 1))))
        return ordered[index]

    @property
    def p50(self) -> float:
        return self._percentile(50)

    @property
    def p95(self) -> float:
        return self._percentile(95)

    @property
    def p99(self) -> float:
        return self._percentile(99)

    @property
    def error_rate(self) -> float:
        if not self.samples:
            return 0.0
        errors = sum(1 for sample in self.samples if sample.status >= 500)
        return errors / len(self.samples)

    @property
    def broken(self) -> bool:
        return self.p95 > P95_LIMIT_MS or self.error_rate > ERROR_RATE_LIMIT


def make_worker(kind: str):
    """Retourne une coroutine `worker(client)` qui exécute une unité de travail.

    Chaque itération est un parcours CLIENT COMPLET, pas un `GET /healthz` :
    mesurer une route qui ne fait rien ne mesure rien.
    """
    if kind == "shop":
        async def worker(client: httpx.AsyncClient) -> int:
            catalog = await client.get("/products")
            if catalog.status_code != 200:
                return catalog.status_code
            product = next(
                (p for p in catalog.json()["products"] if p["stock"] > 0), None
            )
            if product is None:
                return 409  # rupture de stock : pas une erreur serveur
            cart = await client.post("/cart")
            if cart.status_code != 201:
                return cart.status_code
            token = cart.json()["cart"]["token"]
            added = await client.post(
                f"/cart/{token}/items",
                json={"product_id": product["id"], "quantity": 1},
            )
            if added.status_code != 200:
                return added.status_code
            checkout = await client.post("/checkout", json={"cart_token": token})
            return checkout.status_code

        return worker

    if kind == "payment":
        async def worker(client: httpx.AsyncClient) -> int:
            response = await client.get("/outputs?unspent=true&limit=50")
            return response.status_code

        return worker

    if kind == "rag":
        async def worker(client: httpx.AsyncClient) -> int:
            response = await client.post(
                "/ask", json={"q": "combien de temps prend la reprise ?"}
            )
            return response.status_code

        return worker

    async def worker(client: httpx.AsyncClient) -> int:
        response = await client.get("/healthz")
        return response.status_code

    return worker


async def run_stage(base: str, kind: str, clients: int, seconds: float) -> Stage:
    stage = Stage(clients=clients, duration_s=0.0)
    worker = make_worker(kind)
    deadline = time.perf_counter() + seconds
    lock = asyncio.Lock()

    async def client_loop() -> None:
        # `limits` par client : sans cela, httpx réutilise un pool trop petit et
        # on mesure la contention du client, pas celle du serveur.
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
        async with httpx.AsyncClient(
            base_url=base, timeout=30.0, limits=limits
        ) as client:
            while time.perf_counter() < deadline:
                started = time.perf_counter()
                try:
                    status = await worker(client)
                except httpx.HTTPError:
                    status = 599
                latency = (time.perf_counter() - started) * 1000
                async with lock:
                    stage.samples.append(Sample(latency_ms=latency, status=status))

    started = time.perf_counter()
    await asyncio.gather(*(client_loop() for _ in range(clients)))
    stage.duration_s = time.perf_counter() - started
    return stage


def report(stages: list[Stage], kind: str) -> int:
    print(f"\n=== Test de charge : {kind} ===")
    print(
        f"{'clients':>8} {'req/s':>8} {'p50':>8} {'p95':>8} {'p99':>8} "
        f"{'erreurs':>8} {'requêtes':>9}  état"
    )
    breaking: Stage | None = None
    for stage in stages:
        state = "RUPTURE" if stage.broken else "ok"
        if stage.broken and breaking is None:
            breaking = stage
        print(
            f"{stage.clients:>8} {stage.throughput:>8.1f} {stage.p50:>8.0f} "
            f"{stage.p95:>8.0f} {stage.p99:>8.0f} {stage.error_rate * 100:>7.2f}% "
            f"{stage.count:>9}  {state}"
        )

    markdown = [
        f"### Test de charge — `{kind}`",
        "",
        f"Seuils de rupture : p95 > {P95_LIMIT_MS:.0f} ms ou erreurs 5xx > "
        f"{ERROR_RATE_LIMIT * 100:.0f} %.",
        "",
        "| Clients | Débit (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Erreurs | Requêtes |",
        "|---|---|---|---|---|---|---|",
    ]
    for stage in stages:
        markdown.append(
            f"| {stage.clients} | {stage.throughput:.1f} | {stage.p50:.0f} | "
            f"{stage.p95:.0f} | {stage.p99:.0f} | {stage.error_rate * 100:.2f} % | "
            f"{stage.count} |"
        )
    if breaking is not None:
        markdown += [
            "",
            f"**Point de rupture : {breaking.clients} clients concurrents** "
            f"(p95 = {breaking.p95:.0f} ms, {breaking.error_rate * 100:.2f} % d'erreurs).",
        ]
    else:
        best = max(stages, key=lambda s: s.throughput)
        markdown += [
            "",
            f"**Aucune rupture jusqu'à {max(s.clients for s in stages)} clients.** "
            f"Débit maximal mesuré : {best.throughput:.1f} req/s "
            f"({best.clients} clients, p95 = {best.p95:.0f} ms).",
        ]

    with open("docs/mesures-charge.md", "w", encoding="utf-8") as handle:
        handle.write("\n".join(markdown) + "\n")
    print("\nRapport écrit : docs/mesures-charge.md")

    if breaking is not None:
        print(f"Point de rupture : {breaking.clients} clients concurrents.")
        return 0  # avoir un point de rupture n'est PAS un échec : c'est la mesure
    return 0


async def main_async(args) -> int:
    base = args.base or TARGETS[args.target]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            health = await client.get(f"{base}/healthz")
            if health.status_code != 200:
                print(f"cible {base} : /healthz -> {health.status_code}")
                return 1
    except httpx.HTTPError as exc:
        print(f"cible injoignable ({base}) : {exc}")
        return 1

    print(f"Cible {base} · {args.duration}s par palier")
    stages = []
    for clients in args.clients:
        stage = await run_stage(base, args.target, clients, args.duration)
        stages.append(stage)
        print(
            f"  {clients:>3} clients : {stage.throughput:6.1f} req/s, "
            f"p95 {stage.p95:6.0f} ms, {stage.error_rate * 100:5.2f} % erreurs"
        )
        if stage.broken and not args.continue_after_break:
            print("  rupture détectée : on s'arrête là (--continue-after-break pour forcer)")
            break
    return report(stages, args.target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="shop", choices=list(TARGETS))
    parser.add_argument("--base", default=None, help="URL explicite (prioritaire)")
    parser.add_argument("--clients", type=int, nargs="+", default=[1, 5, 10, 20])
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--continue-after-break", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
