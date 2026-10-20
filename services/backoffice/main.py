"""Back-office RESILIENCE-ALPHA — authentification forte et habilitations.

Flux imposé par la consigne additionnelle :

    1. identifiant
    2. caméra USB (sur le PC hôte) → reconnaissance faciale
       - reconnu  → saisie du mot de passe
       - refusé   → identifiant remis à vide, caméra éteinte, 1 nouvel essai
    3. mot de passe
       - valide   → session, LED verte fixe 30 s, accès au back-office
       - invalide → LED rouge 10 clignotements, retour à l'étape 1
    4. après 2 échecs → blocage 5 minutes (compteur côté serveur)

La caméra est ouverte par le navigateur du PC hôte (`getUserMedia`) : l'image
ne transite que vers ce service, sur le réseau local. Le bandeau LED ESP8266
interroge `/led/state` ; il n'est jamais dans le chemin d'authentification.

Lancement :  uvicorn main:create_app --factory --host 0.0.0.0 --port 8086
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import Counter
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import Config
from db import Database
from faces import build_engine
from led import LedController
from security import (
    PolicyError,
    constant_time_equals,
    dummy_verify,
    hash_password,
    hash_token,
    new_challenge_id,
    new_session_token,
    normalize_username,
    validate_password,
    validate_role,
    validate_username,
    verify_password,
)

LOG = logging.getLogger("backoffice")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Modèles de requête
# ---------------------------------------------------------------------------
class BootstrapRequest(BaseModel):
    username: str
    password: str
    token: str
    image_b64: str


class IdentifyStartRequest(BaseModel):
    username: str


class IdentifyRequest(BaseModel):
    challenge_id: str
    image_b64: str


class PasswordRequest(BaseModel):
    challenge_id: str
    password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "operator"


class UserUpdateRequest(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = None


class FaceRequest(BaseModel):
    image_b64: str


# ---------------------------------------------------------------------------
# Fabrique d'application (testable sans variable d'environnement)
# ---------------------------------------------------------------------------
def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.from_env()
    db = Database(cfg.db_path)
    face, face_error = build_engine(cfg)
    led = LedController(cfg.led_token, cfg.led_enabled)

    counters: Counter = Counter()
    counters_lock = threading.Lock()

    def bump(name: str) -> None:
        with counters_lock:
            counters[name] += 1

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        led.off()
        if face is None:
            LOG.error("reconnaissance faciale indisponible : %s", face_error)
        elif face.name == "stub":
            LOG.warning("moteur facial STUB actif — développement uniquement")
        if cfg.bootstrap_token:
            LOG.info("bootstrap administrateur activé (jeton configuré)")
        elif bootstrap_required():
            LOG.warning(
                "aucun administrateur enrôlé et BO_BOOTSTRAP_TOKEN vide : "
                "le back-office est inaccessible. Renseigner le jeton dans .env "
                "puis redémarrer ce service."
            )
        yield
        db.close()

    app = FastAPI(
        title="backoffice",
        version="1.0.0",
        description="Authentification visage + mot de passe, habilitations, bandeau LED",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-LED-Token"],
    )
    app.state.cfg = cfg
    app.state.db = db
    app.state.face = face
    app.state.face_error = face_error
    app.state.led = led

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def fail(status_code: int, reason: str, message: str, **extra):
        detail = {"reason": reason, "message": message, **extra}
        headers = {}
        if status_code == 429 and "retry_after" in extra:
            headers["Retry-After"] = str(int(extra["retry_after"]))
        raise HTTPException(status_code=status_code, detail=detail, headers=headers)

    def audit(username: str | None, event: str, ip: str, **detail) -> None:
        db.run(
            "INSERT INTO audit(ts, username, event, ip, detail) VALUES(?,?,?,?,?)",
            (
                time.time(),
                username,
                event,
                ip,
                json.dumps(detail, ensure_ascii=False, sort_keys=True) if detail else None,
            ),
        )
        bump(event)

    def public_user(row) -> dict:
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "active": bool(row["active"]),
            "face_enrolled": row["face_embedding"] is not None,
            "face_enrolled_at": row["face_enrolled_at"],
            "created_at": row["created_at"],
            "last_login_at": row["last_login_at"],
        }

    def bootstrap_required() -> bool:
        return (
            db.one(
                "SELECT 1 FROM users WHERE role='admin' AND active=1 "
                "AND face_embedding IS NOT NULL LIMIT 1"
            )
            is None
        )

    def active_admins() -> int:
        return int(db.scalar("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1") or 0)

    def lockout_state(username: str) -> dict:
        row = db.one(
            "SELECT failures, locked_until FROM attempts WHERE username=?", (username,)
        )
        fresh = {"locked": False, "attempts_left": cfg.max_attempts, "retry_after": 0}
        if row is None:
            return fresh
        now = time.time()
        locked_until = float(row["locked_until"] or 0)
        if locked_until > now:
            return {
                "locked": True,
                "attempts_left": 0,
                "retry_after": max(1, int(math.ceil(locked_until - now))),
            }
        if locked_until:
            # Le blocage a expiré : on repart avec deux essais neufs.
            db.run("DELETE FROM attempts WHERE username=?", (username,))
            return fresh
        return {
            "locked": False,
            "attempts_left": max(0, cfg.max_attempts - int(row["failures"])),
            "retry_after": 0,
        }

    def register_failure(username: str) -> dict:
        now = time.time()
        row = db.one(
            "SELECT failures, locked_until FROM attempts WHERE username=?", (username,)
        )
        if row and float(row["locked_until"] or 0) > now:
            return {
                "locked": True,
                "attempts_left": 0,
                "retry_after": max(1, int(math.ceil(float(row["locked_until"]) - now))),
            }
        failures = int(row["failures"]) + 1 if row else 1
        locked = failures >= cfg.max_attempts
        locked_until = now + cfg.lockout_seconds if locked else 0.0
        db.run(
            "INSERT INTO attempts(username, failures, locked_until, updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET "
            "failures=excluded.failures, locked_until=excluded.locked_until, "
            "updated_at=excluded.updated_at",
            (username, failures, locked_until, now),
        )
        if locked:
            bump("locked")
        return {
            "locked": locked,
            "attempts_left": max(0, cfg.max_attempts - failures),
            "retry_after": max(0, int(math.ceil(locked_until - now))) if locked else 0,
        }

    def reset_failures(username: str) -> None:
        db.run("DELETE FROM attempts WHERE username=?", (username,))

    def challenge_or_fail(challenge_id: str, require_face: bool = False):
        db.purge_expired()
        row = db.one("SELECT * FROM challenges WHERE id=?", (challenge_id,))
        if row is None:
            fail(400, "challenge_unknown", "recommencez l'identification")
        if row["used"]:
            fail(400, "challenge_used", "cette identification est terminée")
        if float(row["expires_at"]) < time.time():
            fail(400, "challenge_expired", "délai dépassé, recommencez")
        if require_face and not row["face_ok"]:
            fail(400, "face_required", "la reconnaissance faciale n'a pas été validée")
        return row

    def create_session(user, request: Request, now: float) -> str:
        token = new_session_token()
        db.run(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, "
            "last_seen, ip, user_agent) VALUES(?,?,?,?,?,?,?)",
            (
                hash_token(token),
                user["id"],
                now,
                now + cfg.session_ttl_seconds,
                now,
                client_ip(request),
                (request.headers.get("user-agent") or "")[:256],
            ),
        )
        return token

    def current_user(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401,
                detail={"reason": "missing_token", "message": "session requise"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization.split(" ", 1)[1].strip()
        row = db.one(
            "SELECT s.id AS session_id, s.expires_at, s.last_seen, "
            "       u.id, u.username, u.role, u.active "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.revoked = 0",
            (hash_token(token),),
        )
        now = time.time()
        if row is None or not row["active"]:
            raise HTTPException(
                status_code=401,
                detail={"reason": "invalid_session", "message": "session invalide"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        if float(row["expires_at"]) < now or float(row["last_seen"]) < now - cfg.session_idle_seconds:
            db.run("UPDATE sessions SET revoked = 1 WHERE id = ?", (row["session_id"],))
            raise HTTPException(
                status_code=401,
                detail={"reason": "session_expired", "message": "session expirée"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        db.run("UPDATE sessions SET last_seen = ? WHERE id = ?", (now, row["session_id"]))
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "session_id": row["session_id"],
        }

    def require_role(*roles: str):
        def dependency(user: dict = Depends(current_user)) -> dict:
            if user["role"] not in roles:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "reason": "forbidden",
                        "message": f"rôle {user['role']} insuffisant",
                    },
                )
            return user

        return dependency

    # ------------------------------------------------------------------
    # Santé et métriques
    # ------------------------------------------------------------------
    @app.get("/healthz")
    def healthz():
        try:
            db.scalar("SELECT 1")
            db_ok = True
        except Exception:  # noqa: BLE001
            db_ok = False
        face_status = "unavailable"
        if face is not None:
            face_status = "stub" if face.name == "stub" else "ready"
        now = time.time()
        return {
            "status": "ok" if db_ok and face is not None else "degraded",
            "service": "backoffice",
            "version": "1.0.0",
            "db": "ok" if db_ok else "error",
            "face_engine": face_status,
            "face_model": getattr(face, "name", "none"),
            "face_threshold": cfg.face_threshold,
            "face_error": face_error,
            "liveness": "not_implemented",
            "demo_only": face is not None and face.name == "stub",
            "bootstrap_required": bootstrap_required(),
            "users_total": int(db.scalar("SELECT COUNT(*) FROM users") or 0),
            "sessions_active": int(
                db.scalar(
                    "SELECT COUNT(*) FROM sessions WHERE revoked = 0 AND expires_at > ?",
                    (now,),
                )
                or 0
            ),
            "lockouts_active": int(
                db.scalar("SELECT COUNT(*) FROM attempts WHERE locked_until > ?", (now,))
                or 0
            ),
            "led": led.health(),
        }

    @app.get("/metrics")
    def metrics():
        now = time.time()
        face_ready = 1 if face is not None else 0
        stub = 1 if face is not None and face.name == "stub" else 0
        led_health = led.health()
        last_seen = led_health["last_seen_seconds"]
        lines = [
            "# HELP bo_up 1 si le service répond",
            "# TYPE bo_up gauge",
            "bo_up 1",
            "# HELP bo_face_engine_ready 1 si le moteur facial est chargé",
            "# TYPE bo_face_engine_ready gauge",
            f"bo_face_engine_ready {face_ready}",
            "# HELP bo_face_engine_stub 1 si le moteur de développement est actif",
            "# TYPE bo_face_engine_stub gauge",
            f"bo_face_engine_stub {stub}",
            "# HELP bo_bootstrap_required 1 si aucun administrateur n'a de visage enrôlé",
            "# TYPE bo_bootstrap_required gauge",
            f"bo_bootstrap_required {1 if bootstrap_required() else 0}",
            "# HELP bo_users_total nombre d'utilisateurs",
            "# TYPE bo_users_total gauge",
            f"bo_users_total {int(db.scalar('SELECT COUNT(*) FROM users') or 0)}",
            "# HELP bo_sessions_active sessions valides",
            "# TYPE bo_sessions_active gauge",
            f"bo_sessions_active {int(db.scalar('SELECT COUNT(*) FROM sessions WHERE revoked=0 AND expires_at > ?', (now,)) or 0)}",
            "# HELP bo_lockouts_active comptes actuellement bloqués",
            "# TYPE bo_lockouts_active gauge",
            f"bo_lockouts_active {int(db.scalar('SELECT COUNT(*) FROM attempts WHERE locked_until > ?', (now,)) or 0)}",
            "# HELP bo_audit_events_total nombre d'événements d'audit",
            "# TYPE bo_audit_events_total counter",
            f"bo_audit_events_total {int(db.scalar('SELECT COUNT(*) FROM audit') or 0)}",
            "# HELP bo_led_last_seen_seconds secondes depuis le dernier appel de l'ESP8266 (-1 = jamais)",
            "# TYPE bo_led_last_seen_seconds gauge",
            f"bo_led_last_seen_seconds {last_seen if last_seen is not None else -1}",
            "# HELP bo_led_polls_total nombre d'appels reçus du bandeau LED",
            "# TYPE bo_led_polls_total counter",
            f"bo_led_polls_total {led_health['polls']}",
            "# HELP bo_auth_attempts_total étapes d'authentification",
            "# TYPE bo_auth_attempts_total counter",
        ]
        with counters_lock:
            snapshot = dict(counters)
        for result in (
            "identify_start",
            "face_ok",
            "face_fail",
            "password_ok",
            "password_fail",
            "locked",
        ):
            lines.append(f'bo_auth_attempts_total{{result="{result}"}} {snapshot.get(result, 0)}')
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    # ------------------------------------------------------------------
    # Bandeau LED (l'ESP8266 est le seul client)
    # ------------------------------------------------------------------
    @app.get("/led/state")
    def led_state(
        device: str = "unknown",
        token: str = "",
        x_led_token: str | None = Header(default=None),
    ):
        try:
            return led.snapshot(device, x_led_token or token)
        except PermissionError:
            fail(401, "led_token", "jeton du bandeau LED invalide")

    # ------------------------------------------------------------------
    # Bootstrap : premier administrateur (visage + mot de passe)
    # ------------------------------------------------------------------
    @app.get("/auth/bootstrap/status")
    def bootstrap_status():
        return {
            "required": bootstrap_required(),
            "token_configured": bool(cfg.bootstrap_token),
        }

    @app.post("/auth/bootstrap", status_code=201)
    def bootstrap(req: BootstrapRequest, request: Request):
        ip = client_ip(request)
        if not cfg.bootstrap_token:
            fail(403, "bootstrap_disabled", "bootstrap désactivé (BO_BOOTSTRAP_TOKEN vide)")
        if not constant_time_equals(req.token, cfg.bootstrap_token):
            audit(req.username, "bootstrap_denied", ip, reason="bad_token")
            fail(403, "bootstrap_token", "jeton de bootstrap invalide")
        if not bootstrap_required():
            fail(409, "already_bootstrapped", "un administrateur est déjà enrôlé")
        try:
            username = validate_username(req.username)
            password = validate_password(req.password)
        except PolicyError as exc:
            fail(400, "policy", str(exc))
        if face is None:
            fail(503, "face_engine_unavailable", face_error or "moteur facial indisponible")
        result = face.embed_b64(req.image_b64)
        if not result.ok:
            fail(400, result.reason, "visage non exploitable", faces=result.faces)

        now = time.time()
        with db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            if existing and existing["role"] != "admin":
                fail(409, "username_taken", "cet identifiant appartient à un autre rôle")
            if existing:
                conn.execute(
                    "UPDATE users SET password_hash=?, role='admin', active=1, "
                    "face_embedding=?, face_enrolled_at=? WHERE id=?",
                    (
                        hash_password(password, cfg.pbkdf2_iterations),
                        result.embedding,
                        now,
                        existing["id"],
                    ),
                )
                user_id = existing["id"]
            else:
                cursor = conn.execute(
                    "INSERT INTO users(username, password_hash, role, active, "
                    "face_embedding, face_enrolled_at, created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        username,
                        hash_password(password, cfg.pbkdf2_iterations),
                        "admin",
                        1,
                        result.embedding,
                        now,
                        now,
                    ),
                )
                user_id = int(cursor.lastrowid)

        audit(username, "bootstrap", ip, user_id=user_id)
        LOG.info("administrateur %s enrôlé (visage + mot de passe)", username)
        return {
            "user": public_user(db.one("SELECT * FROM users WHERE id=?", (user_id,))),
            "message": "administrateur enrôlé ; connectez-vous avec le visage puis le mot de passe",
        }

    # ------------------------------------------------------------------
    # Étape 1 : identifiant
    # ------------------------------------------------------------------
    @app.post("/auth/identify/start")
    def identify_start(req: IdentifyStartRequest, request: Request):
        ip = client_ip(request)
        try:
            username = validate_username(req.username)
        except PolicyError as exc:
            fail(400, "policy", str(exc))
        state = lockout_state(username)
        if state["locked"]:
            audit(username, "locked", ip, phase="identify_start")
            fail(
                429,
                "locked",
                "trop de tentatives : compte bloqué",
                locked=True,
                retry_after=state["retry_after"],
            )
        db.purge_expired()
        challenge_id = new_challenge_id()
        now = time.time()
        db.run(
            "INSERT INTO challenges(id, username, ip, created_at, expires_at) "
            "VALUES(?,?,?,?,?)",
            (challenge_id, username, ip, now, now + cfg.challenge_ttl_seconds),
        )
        audit(username, "identify_start", ip)
        return {
            "challenge_id": challenge_id,
            "expires_in": cfg.challenge_ttl_seconds,
            "camera": "required",
            "locked": False,
            "attempts_left": state["attempts_left"],
        }

    # ------------------------------------------------------------------
    # Étape 2 : reconnaissance faciale
    # ------------------------------------------------------------------
    @app.post("/auth/identify")
    def identify(req: IdentifyRequest, request: Request):
        ip = client_ip(request)
        challenge = challenge_or_fail(req.challenge_id)
        username = challenge["username"]
        state = lockout_state(username)
        if state["locked"]:
            db.run("UPDATE challenges SET used = 1 WHERE id = ?", (challenge["id"],))
            audit(username, "locked", ip, phase="identify")
            fail(
                429,
                "locked",
                "trop de tentatives : compte bloqué",
                locked=True,
                retry_after=state["retry_after"],
            )
        if face is None:
            fail(503, "face_engine_unavailable", face_error or "moteur facial indisponible")

        result = face.embed_b64(req.image_b64)
        user = db.one(
            "SELECT * FROM users WHERE username = ? AND active = 1", (username,)
        )
        recognized = False
        reason = result.reason
        if result.ok:
            if user is not None and user["face_embedding"] is not None:
                similarity = face.similarity(result.embedding, user["face_embedding"])
                if similarity >= cfg.face_threshold:
                    recognized = True
                    reason = "ok"
                else:
                    reason = "not_recognized"
            else:
                # Même réponse pour un compte inconnu, inactif ou sans visage :
                # l'attaquant n'apprend pas quels identifiants existent.
                reason = "not_recognized"

        if recognized:
            now = time.time()
            db.run(
                "UPDATE challenges SET face_ok = 1, expires_at = ? WHERE id = ?",
                (now + cfg.challenge_ttl_seconds, challenge["id"]),
            )
            led.set_state("green_blink")
            audit(username, "face_ok", ip)
            bump("face_ok")
            return {
                "recognized": True,
                "next": "password",
                "expires_in": cfg.challenge_ttl_seconds,
            }

        # Échec : la consigne impose « identifiant remis à vide, caméra éteinte ».
        db.run("UPDATE challenges SET used = 1 WHERE id = ?", (challenge["id"],))
        led.set_state("red_solid", seconds=30)
        # Une image SANS visage (ou avec plusieurs visages) est un problème de
        # cadrage, pas une tentative d'usurpation : elle ne consomme pas l'essai.
        # Seul un visage détecté mais différent de celui enrôlé compte. Cela ne
        # crée aucune faiblesse : sans visage correspondant, on n'authentifie
        # jamais, et un attaquant n'a rien à gagner à multiplier les images vides.
        counts_as_attempt = reason == "not_recognized"
        state = register_failure(username) if counts_as_attempt else lockout_state(username)
        audit(
            username,
            "face_fail",
            ip,
            reason=reason,
            faces=result.faces,
            counted=counts_as_attempt,
        )
        bump("face_fail")
        if state["locked"]:
            audit(username, "locked", ip, phase="identify")
            fail(
                429,
                "locked",
                "visage non reconnu : compte bloqué 5 minutes",
                locked=True,
                retry_after=state["retry_after"],
                face_reason=reason,
            )
        return {
            "recognized": False,
            "next": "retry",
            "reason": reason,
            "locked": False,
            "counted": counts_as_attempt,
            "attempts_left": state["attempts_left"],
            "retry_after": 0,
        }

    # ------------------------------------------------------------------
    # Étape 3 : mot de passe
    # ------------------------------------------------------------------
    @app.post("/auth/password")
    def password_step(req: PasswordRequest, request: Request):
        ip = client_ip(request)
        challenge = challenge_or_fail(req.challenge_id, require_face=True)
        username = challenge["username"]
        state = lockout_state(username)
        if state["locked"]:
            db.run("UPDATE challenges SET used = 1 WHERE id = ?", (challenge["id"],))
            audit(username, "locked", ip, phase="password")
            fail(
                429,
                "locked",
                "trop de tentatives : compte bloqué",
                locked=True,
                retry_after=state["retry_after"],
            )

        user = db.one(
            "SELECT * FROM users WHERE username = ? AND active = 1", (username,)
        )
        if user is None:
            valid = dummy_verify(req.password, cfg.pbkdf2_iterations)
        else:
            valid = verify_password(req.password, user["password_hash"])

        if valid:
            now = time.time()
            token = create_session(user, request, now)
            db.run("UPDATE challenges SET used = 1 WHERE id = ?", (challenge["id"],))
            db.run("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user["id"]))
            reset_failures(username)
            led.set_state("green_solid", seconds=30)
            audit(username, "password_ok", ip)
            bump("password_ok")
            refreshed = db.one("SELECT * FROM users WHERE id=?", (user["id"],))
            return {
                "access_token": token,
                "token_type": "bearer",
                "expires_in": cfg.session_ttl_seconds,
                "user": public_user(refreshed),
            }

        # Échec : LED rouge 10 clignotements, retour à l'étape 1.
        db.run("UPDATE challenges SET used = 1 WHERE id = ?", (challenge["id"],))
        led.set_state("red_blink", blinks=10)
        state = register_failure(username)
        audit(username, "password_fail", ip)
        bump("password_fail")
        if state["locked"]:
            audit(username, "locked", ip, phase="password")
            fail(
                429,
                "locked",
                "mot de passe invalide : compte bloqué 5 minutes",
                locked=True,
                retry_after=state["retry_after"],
                next="restart",
            )
        fail(
            401,
            "invalid_credentials",
            "mot de passe invalide",
            next="restart",
            locked=False,
            attempts_left=state["attempts_left"],
        )

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------
    @app.get("/auth/me")
    def me(user: dict = Depends(current_user)):
        row = db.one("SELECT * FROM users WHERE id = ?", (user["id"],))
        return {"user": public_user(row)}

    @app.post("/auth/logout", status_code=204)
    def logout(request: Request, user: dict = Depends(current_user)):
        db.run("UPDATE sessions SET revoked = 1 WHERE id = ?", (user["session_id"],))
        led.off()
        audit(user["username"], "logout", client_ip(request))
        return Response(status_code=204)

    # ------------------------------------------------------------------
    # Habilitations (administrateur)
    # ------------------------------------------------------------------
    @app.get("/admin/users")
    def list_users(user: dict = Depends(require_role("admin"))):
        rows = db.query("SELECT * FROM users ORDER BY username")
        result = []
        for row in rows:
            state = lockout_state(row["username"])
            result.append(
                {
                    **public_user(row),
                    "locked": state["locked"],
                    "attempts_left": state["attempts_left"],
                    "retry_after": state["retry_after"],
                }
            )
        return {"users": result}

    @app.post("/admin/users", status_code=201)
    def create_user(
        req: UserCreateRequest,
        request: Request,
        user: dict = Depends(require_role("admin")),
    ):
        ip = client_ip(request)
        try:
            username = validate_username(req.username)
            password = validate_password(req.password)
            role = validate_role(req.role)
        except PolicyError as exc:
            fail(400, "policy", str(exc))
        if db.one("SELECT 1 FROM users WHERE username = ?", (username,)):
            fail(409, "username_taken", "cet identifiant existe déjà")
        now = time.time()
        user_id = db.run(
            "INSERT INTO users(username, password_hash, role, active, created_at) "
            "VALUES(?,?,?,?,?)",
            (username, hash_password(password, cfg.pbkdf2_iterations), role, 1, now),
        )
        audit(user["username"], "user_created", ip, target=username, role=role)
        return {"user": public_user(db.one("SELECT * FROM users WHERE id=?", (user_id,)))}

    @app.patch("/admin/users/{user_id}")
    def update_user(
        user_id: int,
        req: UserUpdateRequest,
        request: Request,
        user: dict = Depends(require_role("admin")),
    ):
        ip = client_ip(request)
        target = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        if target is None:
            fail(404, "user_unknown", "utilisateur inconnu")

        changes: dict = {}
        if req.role is not None:
            try:
                changes["role"] = validate_role(req.role)
            except PolicyError as exc:
                fail(400, "policy", str(exc))
        if req.active is not None:
            changes["active"] = 1 if req.active else 0
        if req.password is not None:
            try:
                changes["password_hash"] = hash_password(
                    validate_password(req.password), cfg.pbkdf2_iterations
                )
            except PolicyError as exc:
                fail(400, "policy", str(exc))

        becomes_non_admin = (
            target["role"] == "admin"
            and (
                changes.get("role", "admin") != "admin"
                or changes.get("active", int(target["active"])) == 0
            )
        )
        if becomes_non_admin and active_admins() <= 1:
            fail(
                409,
                "last_admin",
                "impossible de désactiver ou rétrograder le dernier administrateur",
            )

        if not changes:
            return {"user": public_user(target), "changed": []}

        assignments = ", ".join(f"{column} = ?" for column in changes)
        db.run(
            f"UPDATE users SET {assignments} WHERE id = ?",
            (*changes.values(), user_id),
        )
        # Un mot de passe changé ou un compte désactivé doit fermer les
        # sessions ouvertes : sinon l'ancien titulaire garde l'accès.
        if "password_hash" in changes or changes.get("active") == 0:
            db.run(
                "UPDATE sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0",
                (user_id,),
            )
        audit(
            user["username"],
            "user_updated",
            ip,
            target=target["username"],
            fields=[key for key in changes if key != "password_hash"],
            password_changed="password_hash" in changes,
        )
        return {
            "user": public_user(db.one("SELECT * FROM users WHERE id=?", (user_id,))),
            "changed": [key for key in changes if key != "password_hash"],
        }

    @app.delete("/admin/users/{user_id}")
    def deactivate_user(
        user_id: int,
        request: Request,
        user: dict = Depends(require_role("admin")),
    ):
        ip = client_ip(request)
        target = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        if target is None:
            fail(404, "user_unknown", "utilisateur inconnu")
        if target["role"] == "admin" and active_admins() <= 1:
            fail(409, "last_admin", "impossible de désactiver le dernier administrateur")
        db.run("UPDATE users SET active = 0 WHERE id = ?", (user_id,))
        db.run(
            "UPDATE sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0",
            (user_id,),
        )
        audit(user["username"], "user_deactivated", ip, target=target["username"])
        return {"deactivated": True, "id": user_id}

    @app.post("/admin/users/{user_id}/face")
    def enroll_face(
        user_id: int,
        req: FaceRequest,
        request: Request,
        user: dict = Depends(require_role("admin")),
    ):
        ip = client_ip(request)
        target = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        if target is None:
            fail(404, "user_unknown", "utilisateur inconnu")
        if face is None:
            fail(503, "face_engine_unavailable", face_error or "moteur facial indisponible")
        result = face.embed_b64(req.image_b64)
        if not result.ok:
            fail(400, result.reason, "visage non exploitable", faces=result.faces)
        now = time.time()
        db.run(
            "UPDATE users SET face_embedding = ?, face_enrolled_at = ? WHERE id = ?",
            (result.embedding, now, user_id),
        )
        reset_failures(target["username"])
        audit(user["username"], "face_enrolled", ip, target=target["username"])
        return {
            "user": public_user(db.one("SELECT * FROM users WHERE id=?", (user_id,))),
            "faces": result.faces,
        }

    @app.delete("/admin/users/{user_id}/face")
    def remove_face(
        user_id: int,
        request: Request,
        user: dict = Depends(require_role("admin")),
    ):
        ip = client_ip(request)
        target = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        if target is None:
            fail(404, "user_unknown", "utilisateur inconnu")
        db.run(
            "UPDATE users SET face_embedding = NULL, face_enrolled_at = NULL WHERE id = ?",
            (user_id,),
        )
        audit(user["username"], "face_removed", ip, target=target["username"])
        return {"face_enrolled": False, "id": user_id}

    @app.post("/admin/users/{user_id}/unlock")
    def unlock_user(
        user_id: int,
        request: Request,
        user: dict = Depends(require_role("admin")),
    ):
        ip = client_ip(request)
        target = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        if target is None:
            fail(404, "user_unknown", "utilisateur inconnu")
        reset_failures(target["username"])
        audit(user["username"], "unlock", ip, target=target["username"])
        return {"unlocked": True, "username": target["username"]}

    @app.get("/admin/audit")
    def audit_log(
        limit: int = 100,
        event: str | None = None,
        user: dict = Depends(require_role("admin")),
    ):
        limit = max(1, min(int(limit), 500))
        if event:
            rows = db.query(
                "SELECT * FROM audit WHERE event = ? ORDER BY id DESC LIMIT ?",
                (event, limit),
            )
        else:
            rows = db.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        events = []
        for row in rows:
            try:
                detail = json.loads(row["detail"]) if row["detail"] else {}
            except json.JSONDecodeError:
                detail = {"raw": row["detail"]}
            events.append(
                {
                    "id": row["id"],
                    "ts": row["ts"],
                    "username": row["username"],
                    "event": row["event"],
                    "ip": row["ip"],
                    "detail": detail,
                }
            )
        return {"events": events, "limit": limit}

    @app.get("/admin/sessions")
    def list_sessions(user: dict = Depends(require_role("admin"))):
        rows = db.query(
            "SELECT s.id, s.user_id, u.username, s.created_at, s.expires_at, "
            "       s.last_seen, s.ip, s.user_agent "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.revoked = 0 AND s.expires_at > ? ORDER BY s.last_seen DESC",
            (time.time(),),
        )
        return {"sessions": [dict(row) for row in rows]}

    @app.delete("/admin/sessions/{session_id}")
    def revoke_session(
        session_id: int,
        request: Request,
        user: dict = Depends(require_role("admin")),
    ):
        row = db.one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if row is None:
            fail(404, "session_unknown", "session inconnue")
        db.run("UPDATE sessions SET revoked = 1 WHERE id = ?", (session_id,))
        audit(user["username"], "session_revoked", client_ip(request), session_id=session_id)
        return {"revoked": True, "id": session_id}

    @app.get("/admin/stats")
    def stats(user: dict = Depends(require_role("admin"))):
        now = time.time()
        return {
            "users_total": int(db.scalar("SELECT COUNT(*) FROM users") or 0),
            "users_active": int(
                db.scalar("SELECT COUNT(*) FROM users WHERE active = 1") or 0
            ),
            "admins": active_admins(),
            "faces_enrolled": int(
                db.scalar("SELECT COUNT(*) FROM users WHERE face_embedding IS NOT NULL")
                or 0
            ),
            "sessions_active": int(
                db.scalar(
                    "SELECT COUNT(*) FROM sessions WHERE revoked = 0 AND expires_at > ?",
                    (now,),
                )
                or 0
            ),
            "lockouts_active": int(
                db.scalar("SELECT COUNT(*) FROM attempts WHERE locked_until > ?", (now,))
                or 0
            ),
            "audit_events": int(db.scalar("SELECT COUNT(*) FROM audit") or 0),
            "bootstrap_required": bootstrap_required(),
        }

    # En-têtes de sécurité communs. `no-store` sur l'authentification évite
    # qu'un jeton ou un état de challenge soit mis en cache par un proxy.
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith(("/auth", "/admin")):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


if __name__ == "__main__":
    import uvicorn

    # `--factory` : l'application n'est construite qu'au démarrage du serveur,
    # jamais à l'import (les tests importent ce module sans créer de base).
    uvicorn.run("main:create_app", factory=True, host="0.0.0.0", port=8086)
