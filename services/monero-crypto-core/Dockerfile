# Rust récent obligatoire : les dépendances transitives actuelles (zeroize,
# getrandom, ...) utilisent `edition2024`, refusée par Cargo 1.80. L'image
# `rust:1.80` d'origine faisait échouer le build avec
# « feature `edition2024` is required ».
FROM rust:1.99 AS builder
WORKDIR /app

# Cargo.lock est versionné : on construit avec --locked pour que l'image et le
# poste de dev compilent exactement les mêmes versions de crates. Sans lock,
# une nouvelle publication de crate casse le build du jour de la soutenance.
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/monero-crypto-core /usr/local/bin/monero-crypto-core
EXPOSE 8081
CMD ["monero-crypto-core"]
