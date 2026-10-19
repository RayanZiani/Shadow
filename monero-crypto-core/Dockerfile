FROM rust:1.80 AS builder
WORKDIR /app
COPY Cargo.toml .
COPY src ./src
RUN cargo build --release

FROM debian:bookworm-slim
COPY --from=builder /app/target/release/monero-crypto-core /usr/local/bin/monero-crypto-core
EXPOSE 8081
CMD ["monero-crypto-core"]
