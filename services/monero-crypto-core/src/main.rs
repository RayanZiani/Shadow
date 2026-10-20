// monero-crypto-core — noyau Rust stateless du pilier DEV.
//
// 4 endpoints prévus au plan :
//   POST /crypto/stealth-address   -> IMPLÉMENTÉ (vraie ECDH Ristretto)
//   POST /crypto/ring-sign         -> SQUELETTE : branchez ici votre code de wallet Monero existant (CLSAG)
//   POST /crypto/ring-verify       -> SQUELETTE : mesure le temps CPU, feature clé pour le ML
//   POST /crypto/audit-view        -> SQUELETTE : déchiffrement via la View Key (séparée de la Spend Key)
//   GET  /healthz                  -> liveness, utilisé par docker compose et la recette
//
// Ce fichier compile et répond déjà. Les entrées invalides renvoient 400 (et non
// un panic) : une requête malformée ne doit jamais tuer le worker pendant la
// démonstration.

use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use curve25519_dalek::{
    constants::RISTRETTO_BASEPOINT_POINT,
    ristretto::{CompressedRistretto, RistrettoPoint},
    scalar::Scalar,
};
use rand_core::OsRng;
use serde::{Deserialize, Serialize};
use sha3::{Digest, Keccak256};
use std::time::Instant;

/// Erreur d'entrée renvoyée au client en 400 avec un message lisible.
#[derive(Debug)]
struct BadRequest(String);

impl IntoResponse for BadRequest {
    fn into_response(self) -> Response {
        (StatusCode::BAD_REQUEST, self.0).into_response()
    }
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    let app = Router::new()
        .route("/healthz", get(healthz))
        .route("/crypto/stealth-address", post(stealth_address))
        .route("/crypto/ring-sign", post(ring_sign))
        .route("/crypto/ring-verify", post(ring_verify))
        .route("/crypto/audit-view", post(audit_view));

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8081").await.unwrap();
    tracing::info!("monero-crypto-core listening on :8081");
    axum::serve(listener, app).await.unwrap();
}

async fn healthz() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "ok",
        "service": "monero-crypto-core",
        // Tant que ring-sign/ring-verify/audit-view sont des placeholders, le
        // service le dit : un jury ne doit pas découvrir le TODO en Q&A.
        "implemented": ["stealth-address"],
        "placeholder": ["ring-sign", "ring-verify", "audit-view"],
    }))
}

// ---------- /crypto/stealth-address ----------

#[derive(Deserialize)]
struct StealthRequest {
    public_spend_key_hex: String, // B
    public_view_key_hex: String,  // A
}

#[derive(Serialize)]
struct StealthResponse {
    stealth_address_hex: String, // P = H_s(rA)G + B
    tx_public_key_hex: String,   // R = rG
}

/// Décode un point Ristretto : 64 caractères hexadécimaux, point valide.
/// Toute autre entrée produit une 400 explicite, jamais un panic.
fn point_from_hex(s: &str) -> Result<RistrettoPoint, BadRequest> {
    let bytes = hex::decode(s).map_err(|_| BadRequest("hex invalide".into()))?;
    let arr: [u8; 32] = bytes
        .try_into()
        .map_err(|_| BadRequest("un point Ristretto fait 32 octets".into()))?;
    CompressedRistretto(arr)
        .decompress()
        .ok_or_else(|| BadRequest("point hors courbe".into()))
}

fn hash_to_scalar(point: &RistrettoPoint) -> Scalar {
    let mut hasher = Keccak256::new();
    hasher.update(point.compress().as_bytes());
    let digest = hasher.finalize();
    let mut wide = [0u8; 64];
    wide[..32].copy_from_slice(&digest);
    Scalar::from_bytes_mod_order_wide(&wide)
}

async fn stealth_address(
    Json(req): Json<StealthRequest>,
) -> Result<Json<StealthResponse>, BadRequest> {
    let b_point = point_from_hex(&req.public_spend_key_hex)?;
    let a_point = point_from_hex(&req.public_view_key_hex)?;

    let r_scalar = Scalar::random(&mut OsRng);
    let r_point = r_scalar * RISTRETTO_BASEPOINT_POINT; // R = rG
    let shared = r_scalar * a_point; // rA
    let hs = hash_to_scalar(&shared); // H_s(rA)
    let stealth = hs * RISTRETTO_BASEPOINT_POINT + b_point; // P = H_s(rA)G + B

    Ok(Json(StealthResponse {
        stealth_address_hex: hex::encode(stealth.compress().as_bytes()),
        tx_public_key_hex: hex::encode(r_point.compress().as_bytes()),
    }))
}

// ---------- /crypto/ring-sign ----------

#[derive(Deserialize)]
struct RingSignRequest {
    real_output_id: String,
    decoy_ids: Vec<String>, // tirés par payment-api via GET /decoys
    amount: u64,
    spend_private_key_hex: String,
}

#[derive(Serialize)]
struct RingSignResponse {
    ring_signature_hex: String,      // TODO: vraie CLSAG/MLSAG
    key_image_hex: String,           // I = xH_p(P) — nécessaire à l'anti-double-dépense
    pedersen_commitment_hex: String, // C = aG + bH
    ring_size: usize,
}

async fn ring_sign(Json(req): Json<RingSignRequest>) -> Json<RingSignResponse> {
    // TODO (vous) : remplacez ce placeholder par votre implémentation CLSAG
    // (celle de votre wallet Monero Rust). Gardez la forme de la réponse
    // pour ne pas casser payment-api, qui appelle /crypto/ring-verify derrière.
    let ring_size = req.decoy_ids.len() + 1;
    let placeholder_sig = format!("PLACEHOLDER_CLSAG::{}::{}", req.real_output_id, ring_size);

    Json(RingSignResponse {
        ring_signature_hex: hex::encode(placeholder_sig),
        key_image_hex: hex::encode(format!("KI::{}", req.real_output_id)),
        pedersen_commitment_hex: hex::encode(format!("C::{}", req.amount)),
        ring_size,
    })
}

// ---------- /crypto/ring-verify ----------

#[derive(Deserialize)]
struct RingVerifyRequest {
    ring_signature_hex: String,
    key_image_hex: String,
    pedersen_commitment_hex: String,
}

#[derive(Serialize)]
struct RingVerifyResponse {
    valid: bool,
    verify_cpu_microseconds: u128, // feature clé envoyée au ml-detector
}

async fn ring_verify(Json(req): Json<RingVerifyRequest>) -> Json<RingVerifyResponse> {
    let start = Instant::now();

    // TODO (vous) : vraie vérification mathématique de l'anneau +
    // de l'équilibre des engagements de Pedersen (∑in - ∑out = 0).
    let valid = req.ring_signature_hex.starts_with("504c414345484f4c444552") // "PLACEHOLDER" en hex
        || !req.key_image_hex.is_empty();
    let _ = &req.pedersen_commitment_hex;

    Json(RingVerifyResponse {
        valid,
        verify_cpu_microseconds: start.elapsed().as_micros(),
    })
}

// ---------- /crypto/audit-view ----------
// La killer-feature gouvernance : déchiffrer un montant avec la View Key
// SANS jamais détenir la Spend Key (séparation mathématique, auditable DORA Art.19).

#[derive(Deserialize)]
struct AuditViewRequest {
    tx_public_key_hex: String,    // R
    private_view_key_hex: String, // a (scalaire, PAS un point)
    encrypted_amount_hex: String,
}

#[derive(Serialize)]
struct AuditViewResponse {
    decrypted_amount: u64,
}

async fn audit_view(Json(req): Json<AuditViewRequest>) -> Json<AuditViewResponse> {
    // TODO (vous) : shared = a*R, puis dérivez le masque du montant avec
    // Keccak("amount" || H_s(shared)) et XOR/désobscurcissez le montant,
    // exactement comme le fait un wallet Monero côté view-only.
    let _ = (&req.tx_public_key_hex, &req.private_view_key_hex);
    let placeholder_amount = req.encrypted_amount_hex.len() as u64; // à remplacer
    Json(AuditViewResponse {
        decrypted_amount: placeholder_amount,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Un point Ristretto valide, sérialisé en hex : la base point.
    fn base_point_hex() -> String {
        hex::encode(RISTRETTO_BASEPOINT_POINT.compress().as_bytes())
    }

    #[test]
    fn point_from_hex_accepte_un_point_valide() {
        assert!(point_from_hex(&base_point_hex()).is_ok());
    }

    #[test]
    fn point_from_hex_refuse_une_taille_invalide() {
        // Avant correction, ce cas provoquait un panic du worker tokio.
        assert!(point_from_hex("0000").is_err());
        assert!(point_from_hex("").is_err());
    }

    #[test]
    fn point_from_hex_refuse_un_non_point() {
        // 32 octets, mais pas une encoding Ristretto valide (tout à 0xFF).
        assert!(point_from_hex(&"ff".repeat(32)).is_err());
        // Hex valide mais caractère non hexadécimal.
        assert!(point_from_hex(&"zz".repeat(32)).is_err());
    }

    #[test]
    fn hash_to_scalar_est_deterministe_et_dependant_du_point() {
        let a = hash_to_scalar(&RISTRETTO_BASEPOINT_POINT);
        let b = hash_to_scalar(&RISTRETTO_BASEPOINT_POINT);
        let c = hash_to_scalar(&(RISTRETTO_BASEPOINT_POINT + RISTRETTO_BASEPOINT_POINT));
        assert_eq!(a, b, "même point -> même scalaire");
        assert_ne!(a, c, "point différent -> scalaire différent");
    }
}
