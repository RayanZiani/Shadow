"""Reconnaissance faciale légère — OpenCV YuNet + SFace, CPU uniquement.

Pourquoi ces modèles :

* **YuNet** (232 Ko) détecte les visages et leurs 5 points de repère ;
* **SFace** (37 Mo) produit un descripteur de 128 dimensions ; la similarité
  cosinus entre deux descripteurs du même visage est > 0,363 (documentation
  OpenCV), nous prenons 0,40 par défaut ;
* aucune dépendance à PyTorch, aucun GPU, ~20-40 ms par visage sur un CPU
  portable. C'est le sens de « IA légère » dans la consigne.

Le moteur `stub` n'existe que pour les tests et le développement sans caméra.
Il est refusé en démonstration : `/healthz` l'annonce explicitement et
l'interface affiche un bandeau. Il n'est jamais sélectionné par défaut.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass

import numpy as np


@dataclass
class FaceResult:
    embedding: bytes | None
    reason: str  # ok | no_face | multiple_faces | decode_error | too_large
    faces: int = 0

    @property
    def ok(self) -> bool:
        return self.embedding is not None


class FaceEngine:
    name = "abstract"

    def embed_b64(self, image_b64: str) -> FaceResult:  # pragma: no cover - interface
        raise NotImplementedError

    @staticmethod
    def similarity(left: bytes, right: bytes) -> float:
        a = np.frombuffer(left, dtype=np.float32)
        b = np.frombuffer(right, dtype=np.float32)
        if a.shape != b.shape or a.size == 0:
            return 0.0
        denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denominator == 0.0:
            return 0.0
        return float(np.dot(a, b) / denominator)


def _decode_payload(image_b64: str, max_bytes: int) -> tuple[bytes | None, str]:
    if not isinstance(image_b64, str) or not image_b64.strip():
        return None, "decode_error"
    payload = image_b64.strip()
    if payload.startswith("data:"):
        try:
            payload = payload.split(",", 1)[1]
        except IndexError:
            return None, "decode_error"
    # Les clients JavaScript peuvent omettre le padding.
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None, "decode_error"
    if len(raw) > max_bytes:
        return None, "too_large"
    if not raw:
        return None, "decode_error"
    return raw, "ok"


class OpenCvFaceEngine(FaceEngine):
    name = "sface"

    def __init__(
        self,
        detector_path: str,
        recognizer_path: str,
        max_side: int = 1280,
        max_bytes: int = 6 * 1024 * 1024,
        score_threshold: float = 0.80,
    ):
        import cv2  # import paresseux : les tests sans OpenCV restent exécutables

        self._cv2 = cv2
        self._max_side = max_side
        self._max_bytes = max_bytes
        self._detector = cv2.FaceDetectorYN.create(
            detector_path, "", (320, 320), score_threshold, 0.3, 5000
        )
        self._recognizer = cv2.FaceRecognizerSF.create(recognizer_path, "")

    def embed_b64(self, image_b64: str) -> FaceResult:
        raw, reason = _decode_payload(image_b64, self._max_bytes)
        if raw is None:
            return FaceResult(None, reason)

        cv2 = self._cv2
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return FaceResult(None, "decode_error")

        height, width = image.shape[:2]
        largest = max(height, width)
        if largest > self._max_side:
            scale = self._max_side / float(largest)
            image = cv2.resize(
                image,
                (max(1, int(width * scale)), max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
            height, width = image.shape[:2]

        self._detector.setInputSize((width, height))
        _, faces = self._detector.detect(image)
        if faces is None or len(faces) == 0:
            return FaceResult(None, "no_face", 0)

        # Un visage au premier plan, éventuellement un visage lointain dans le
        # décor : on refuse seulement si le second est comparable au premier.
        ordered = sorted(faces, key=lambda row: float(row[2] * row[3]), reverse=True)
        if len(ordered) > 1:
            first_area = float(ordered[0][2] * ordered[0][3])
            second_area = float(ordered[1][2] * ordered[1][3])
            if first_area > 0 and second_area / first_area > 0.5:
                return FaceResult(None, "multiple_faces", len(ordered))

        aligned = self._recognizer.alignCrop(image, ordered[0])
        feature = self._recognizer.feature(aligned)
        vector = np.asarray(feature, dtype=np.float32).flatten()
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            return FaceResult(None, "decode_error", len(ordered))
        vector /= norm
        return FaceResult(vector.tobytes(), "ok", len(ordered))


class StubFaceEngine(FaceEngine):
    """Moteur déterministe pour les tests — JAMAIS pour la démonstration.

    Le « visage » est un petit JSON encodé en base64 : `{"face": "alice"}`.
    Deux appels avec le même nom donnent le même descripteur ; deux noms
    différents donnent des descripteurs quasi orthogonaux.
    """

    name = "stub"

    def __init__(self, max_bytes: int = 6 * 1024 * 1024):
        self._max_bytes = max_bytes

    def embed_b64(self, image_b64: str) -> FaceResult:
        raw, reason = _decode_payload(image_b64, self._max_bytes)
        if raw is None:
            return FaceResult(None, reason)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return FaceResult(None, "no_face")
        if not isinstance(payload, dict):
            return FaceResult(None, "no_face")
        if payload.get("no_face"):
            return FaceResult(None, "no_face")
        if payload.get("multiple_faces"):
            return FaceResult(None, "multiple_faces", 2)
        name = str(payload.get("face", "")).strip()
        if not name:
            return FaceResult(None, "no_face")

        seed = int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big")
        rng = np.random.default_rng(seed)
        vector = rng.normal(size=128).astype(np.float32)
        vector /= float(np.linalg.norm(vector))
        return FaceResult(vector.tobytes(), "ok", 1)


def build_engine(cfg) -> tuple[FaceEngine | None, str | None]:
    """Retourne (moteur, erreur). Une erreur est PUBLIÉE, jamais masquée."""
    if cfg.face_engine == "stub":
        return StubFaceEngine(cfg.max_image_bytes), None
    try:
        import os

        detector = os.path.join(cfg.face_model_dir, "face_detection_yunet_2023mar.onnx")
        recognizer = os.path.join(
            cfg.face_model_dir, "face_recognition_sface_2021dec.onnx"
        )
        for path in (detector, recognizer):
            if not os.path.exists(path):
                raise FileNotFoundError(f"modèle absent : {path}")
        return (
            OpenCvFaceEngine(
                detector,
                recognizer,
                max_side=cfg.face_max_side,
                max_bytes=cfg.max_image_bytes,
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001 - l'erreur doit être visible dans /healthz
        return None, f"{type(exc).__name__}: {exc}"
