import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "./api.js";
import CameraView from "./CameraView.jsx";
import { captureBase64 } from "./camera.js";
import { saveSession } from "./session.js";

/**
 * Page d'authentification du back-office — parcours imposé par la consigne.
 *
 *   1. identifiant
 *   2. caméra USB → reconnaissance faciale
 *        reconnu  → 3. mot de passe
 *        refusé   → identifiant vidé, caméra éteinte après 30 s de rouge fixe
 *   3. mot de passe
 *        valide   → session ouverte, LED verte fixe 30 s
 *        invalide → LED rouge 10 flashs, retour à l'étape 1
 *   Après 2 échecs : blocage 5 minutes (compteur tenu par le SERVEUR).
 *
 * Le compteur est côté serveur : recharger la page ne le remet pas à zéro, et
 * un attaquant ne peut pas contourner le blocage en vidant son stockage local.
 */

const RED_SOLID_SECONDS = 30; // consigne : rouge fixe 30 s si visage non reconnu
const RED_BLINKS = 10; // consigne : 10 clignotements si mot de passe refusé
const BLINK_MS = 180;

function useCountdown(seconds) {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    setLeft(seconds);
    if (seconds <= 0) return undefined;
    const id = window.setInterval(() => {
      setLeft((value) => {
        if (value <= 1) {
          window.clearInterval(id);
          return 0;
        }
        return value - 1;
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, [seconds]);
  return left;
}

/** Miroir de la LED bicolore physique : même état, même cadence. */
function LedMirror({ state }) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (state !== "green_blink" && state !== "red_blink") {
      setOn(state === "green_solid" || state === "red_solid");
      return undefined;
    }
    setOn(true);
    const id = window.setInterval(() => setOn((value) => !value), BLINK_MS);
    return () => window.clearInterval(id);
  }, [state]);

  const color = state.startsWith("green") ? "#0f9d63" : "#c8102e";
  return (
    <div className="flex items-center gap-2" title={`LED ${state}`}>
      <span
        className="w-4 h-4 rounded-full border border-ink/40 transition-colors"
        style={{
          backgroundColor: state === "off" ? "#2a2a2a" : on ? color : "#1a1a1a",
          boxShadow: state !== "off" && on ? `0 0 12px ${color}` : "none",
        }}
      />
      <span className="text-[11px] font-mono text-ink/60">LED {state}</span>
    </div>
  );
}

function Field({ label, ...props }) {
  return (
    <label className="block mb-3">
      <span className="block text-ink/70 text-xs font-bold uppercase tracking-wide mb-1">
        {label}
      </span>
      <input
        {...props}
        className="w-full rounded-lg border border-rule bg-white/80 px-3 py-2 text-ink text-lg
                   focus:outline-none focus:ring-2 focus:ring-ink/40 disabled:opacity-50"
      />
    </label>
  );
}

function Alert({ tone = "error", children }) {
  const styles =
    tone === "error"
      ? "bg-crimson/10 border-crimson text-crimson"
      : tone === "warn"
        ? "bg-amber-100 border-amber-500 text-amber-800"
        : "bg-emerald/10 border-emerald text-emerald";
  return (
    <div className={`border-l-4 px-3 py-2 rounded text-sm font-bold mb-3 ${styles}`}>
      {children}
    </div>
  );
}

export default function LoginPage({ onAuthenticated, health }) {
  const [step, setStep] = useState("username"); // username | face | password | refused | locked
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState(null);
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [cameraOn, setCameraOn] = useState(false);
  const [ledState, setLedState] = useState("off");
  const [attemptsLeft, setAttemptsLeft] = useState(null);
  const [lockSeconds, setLockSeconds] = useState(0);
  const [blinkLeft, setBlinkLeft] = useState(0);
  const videoRef = useRef(null);

  const refusalLeft = useCountdown(step === "refused" ? RED_SOLID_SECONDS : 0);
  const lockLeft = useCountdown(lockSeconds);

  const bootstrapRequired = health?.bootstrap_required === true;
  const demoOnly = health?.demo_only === true;

  // --- Cycle de la LED simulée (miroir du bandeau ESP8266) ----------------
  useEffect(() => {
    if (ledState === "green_solid") {
      const id = window.setTimeout(() => setLedState("off"), 30_000);
      return () => window.clearTimeout(id);
    }
    if (ledState === "red_solid") {
      const id = window.setTimeout(() => setLedState("off"), 30_000);
      return () => window.clearTimeout(id);
    }
    if (ledState === "red_blink") {
      setBlinkLeft(RED_BLINKS);
      const id = window.setInterval(() => {
        setBlinkLeft((value) => {
          if (value <= 1) {
            window.clearInterval(id);
            setLedState("off");
            return 0;
          }
          return value - 1;
        });
      }, BLINK_MS * 2);
      return () => window.clearInterval(id);
    }
    return undefined;
  }, [ledState]);

  // Fin du rouge fixe : la caméra s'éteint, l'identifiant est vidé.
  useEffect(() => {
    if (step === "refused" && refusalLeft === 0) {
      setCameraOn(false);
      setUsername("");
      setPassword("");
      setChallenge(null);
      setLedState("off");
      setStep(attemptsLeft === 0 ? "locked" : "username");
      if (attemptsLeft === 0) setLockSeconds(300);
    }
  }, [step, refusalLeft, attemptsLeft]);

  const handleCameraError = useCallback((cameraError) => {
    setError(cameraError.message);
    setCameraOn(false);
    setStep("username");
  }, []);

  const reset = (nextMessage) => {
    setCameraOn(false);
    setPassword("");
    setUsername("");
    setChallenge(null);
    setStep("username");
    setMessage(nextMessage ?? null);
  };

  const submitUsername = async (event) => {
    event?.preventDefault();
    if (busy) return;
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const started = await api.identifyStart(username);
      setChallenge(started.challenge_id);
      setAttemptsLeft(started.attempts_left);
      setCameraOn(true);
      setLedState("off");
      setStep("face");
    } catch (apiError) {
      if (apiError instanceof ApiError && apiError.status === 429) {
        setLockSeconds(apiError.detail.retry_after || 300);
        setLedState("off");
        setStep("locked");
      } else {
        setError(apiError.message);
      }
    } finally {
      setBusy(false);
    }
  };

  const scanFace = async () => {
    if (busy || !videoRef.current) return;
    setBusy(true);
    setError(null);
    try {
      const image = await captureBase64(videoRef.current);
      const result = await api.identify(challenge, image);
      if (result.recognized) {
        setLedState("green_blink");
        setStep("password");
        setMessage({
          tone: "ok",
          text: "visage reconnu — saisissez votre mot de passe",
        });
        // La caméra reste allumée pendant la saisie (la consigne éteint la
        // caméra en cas d'échec, pas en cas de succès), mais elle n'est plus
        // sollicitée : aucun flux n'est envoyé.
        return;
      }
      // Refus : identifiant vidé, caméra éteinte après 30 s de rouge fixe.
      setLedState("red_solid");
      setAttemptsLeft(result.attempts_left);
      setMessage({
        tone: "error",
        text:
          result.reason === "no_face"
            ? "aucun visage détecté"
            : result.reason === "multiple_faces"
              ? "plusieurs visages détectés — isolez-vous devant la caméra"
              : "visage non reconnu",
      });
      setStep("refused");
    } catch (apiError) {
      if (apiError instanceof ApiError && apiError.status === 429) {
        setCameraOn(false);
        setLockSeconds(apiError.detail.retry_after || 300);
        setLedState("off");
        setStep("locked");
      } else {
        setError(apiError.message);
      }
    } finally {
      setBusy(false);
    }
  };

  const submitPassword = async (event) => {
    event?.preventDefault();
    if (busy || !challenge) return;
    setBusy(true);
    setError(null);
    try {
      const session = await api.password(challenge, password);
      setLedState("green_solid");
      setCameraOn(false);
      saveSession(session.access_token, session.user);
      setMessage({ tone: "ok", text: "authentification réussie — ouverture du back-office" });
      window.setTimeout(() => onAuthenticated?.(session.user), 350);
    } catch (apiError) {
      if (apiError instanceof ApiError && apiError.status === 429) {
        setCameraOn(false);
        setLedState("red_blink");
        setLockSeconds(apiError.detail.retry_after || 300);
        window.setTimeout(() => setStep("locked"), RED_BLINKS * BLINK_MS * 2);
        return;
      }
      // Mot de passe refusé : 10 flashs rouges puis retour à l'identifiant.
      setLedState("red_blink");
      setAttemptsLeft(apiError.detail?.attempts_left ?? null);
      setMessage({ tone: "error", text: "mot de passe refusé" });
      setStep("blinking");
      window.setTimeout(() => {
        setCameraOn(false);
        setPassword("");
        setUsername("");
        setChallenge(null);
        setStep("username");
      }, RED_BLINKS * BLINK_MS * 2 + 400);
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    try {
      await api.logout();
    } catch {
      // une session déjà expirée n'est pas une erreur pour l'utilisateur
    }
    reset();
  };

  // -----------------------------------------------------------------------
  // Rendu
  // -----------------------------------------------------------------------
  if (bootstrapRequired) {
    return (
      <BootstrapPage
        health={health}
        onDone={() => window.location.reload()}
        demoOnly={demoOnly}
      />
    );
  }

  return (
    <div className="min-h-screen bg-page flex items-center justify-center p-6">
      <div className="w-full max-w-3xl bg-card rounded-2xl shadow-xl p-6">
        <header className="flex items-start justify-between mb-4 gap-4">
          <div>
            <h1 className="text-ink text-xl font-bold">RESILIENCE-ALPHA — Back-office</h1>
            <p className="text-ink/70 text-sm">
              Authentification à deux facteurs : visage (caméra USB du PC hôte) puis mot de passe
            </p>
          </div>
          <LedMirror state={ledState} />
        </header>

        {demoOnly && (
          <Alert tone="warn">
            moteur facial de démonstration (STUB) — aucune reconnaissance réelle.
            Reconstruire l'image du back-office pour activer YuNet + SFace.
          </Alert>
        )}
        {health?.face_engine === "unavailable" && (
          <Alert tone="error">
            moteur facial indisponible : {health.face_error}
          </Alert>
        )}

        <div className="grid md:grid-cols-2 gap-6">
          {/* Colonne caméra : toujours visible, pour montrer qu'elle est éteinte */}
          <div>
            <CameraView
              active={cameraOn}
              videoRef={videoRef}
              onError={handleCameraError}
              label="CAMÉRA ACTIVE"
            />
            <p className="text-center text-ink/60 text-[11px] mt-2">
              caméra USB branchée sur le PC hôte · aucune image n'est enregistrée
            </p>
          </div>

          {/* Colonne formulaire */}
          <div>
            {message && <Alert tone={message.tone}>{message.text}</Alert>}
            {error && <Alert tone="error">{error}</Alert>}

            {step === "username" && (
              <form onSubmit={submitUsername}>
                <Field
                  label="Identifiant"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  autoComplete="username"
                  autoFocus
                  placeholder="prenom.nom"
                />
                <button
                  type="submit"
                  disabled={busy || username.trim().length < 3}
                  className="w-full bg-ink text-white font-bold rounded-lg py-2.5 disabled:opacity-40"
                >
                  {busy ? "vérification…" : "Continuer → caméra"}
                </button>
                <p className="text-ink/60 text-[11px] mt-3">
                  Étape 1/3 · l'identifiant seul ne donne accès à rien : la caméra
                  confirme qui vous êtes avant la saisie du mot de passe.
                </p>
                {attemptsLeft !== null && attemptsLeft < 2 && (
                  <p className="text-crimson text-xs font-bold mt-2">
                    {attemptsLeft} tentative(s) restante(s) avant blocage 5 min
                  </p>
                )}
              </form>
            )}

            {step === "face" && (
              <div>
                <p className="text-ink font-bold mb-1">Étape 2/3 · reconnaissance faciale</p>
                <p className="text-ink/70 text-sm mb-4">
                  Regardez la caméra, puis lancez la reconnaissance. Le résultat est
                  comparé au visage enrôlé pour <b>{username}</b>.
                </p>
                <button
                  onClick={scanFace}
                  disabled={busy}
                  className="w-full bg-ink text-white font-bold rounded-lg py-2.5 disabled:opacity-40"
                >
                  {busy ? "analyse…" : "Scanner mon visage"}
                </button>
                <button
                  onClick={() => {
                    setCameraOn(false);
                    reset(null);
                  }}
                  className="w-full mt-2 text-ink/60 text-xs font-bold underline"
                >
                  annuler
                </button>
              </div>
            )}

            {step === "refused" && (
              <div>
                <Alert tone="error">
                  visage non reconnu — identifiant remis à vide dans {refusalLeft} s
                </Alert>
                <p className="text-ink/70 text-sm">
                  La LED passe au <b>rouge fixe pendant 30 secondes</b>, puis la caméra
                  s'éteint et vous pouvez ressaisir votre identifiant.
                </p>
                <p className="text-ink/60 text-[11px] mt-3">
                  Tentatives restantes : {attemptsLeft ?? "?"} · après 2 échecs, blocage
                  de 5 minutes.
                </p>
              </div>
            )}

            {step === "blinking" && (
              <div className="text-center py-8">
                <p className="text-crimson text-2xl font-bold">MOT DE PASSE REFUSÉ</p>
                <p className="text-ink/70 text-sm mt-2">
                  LED rouge · {RED_BLINKS} clignotements · retour à l'identification
                </p>
                <p className="text-ink/60 text-[11px] mt-2">flashs restants : {blinkLeft}</p>
              </div>
            )}

            {step === "password" && (
              <form onSubmit={submitPassword}>
                <p className="text-ink font-bold mb-1">Étape 3/3 · mot de passe</p>
                <p className="text-ink/70 text-sm mb-3">
                  visage validé pour <b>{username}</b>
                </p>
                <Field
                  label="Mot de passe"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  autoFocus
                />
                <button
                  type="submit"
                  disabled={busy || password.length < 1}
                  className="w-full bg-ink text-white font-bold rounded-lg py-2.5 disabled:opacity-40"
                >
                  {busy ? "ouverture…" : "Ouvrir le back-office"}
                </button>
                <button
                  type="button"
                  onClick={() => reset(null)}
                  className="w-full mt-2 text-ink/60 text-xs font-bold underline"
                >
                  recommencer l'identification
                </button>
              </form>
            )}

            {step === "locked" && (
              <div>
                <Alert tone="error">
                  compte bloqué — {lockLeft > 0 ? `${lockLeft} s` : "0 s"} restantes
                </Alert>
                <p className="text-ink/70 text-sm">
                  Deux tentatives ont échoué. Le blocage de <b>5 minutes</b> est tenu par
                  le serveur : recharger la page ou changer de navigateur ne le lève pas.
                  Un administrateur peut débloquer le compte depuis l'écran
                  « Habilitations ».
                </p>
                <button
                  onClick={() => {
                    setLockSeconds(0);
                    reset(null);
                  }}
                  className="w-full mt-4 bg-ink text-white font-bold rounded-lg py-2.5"
                >
                  Réessayer
                </button>
              </div>
            )}

            {step !== "username" && step !== "locked" && (
              <button
                onClick={logout}
                className="w-full mt-4 text-ink/50 text-[11px] underline"
              >
                abandonner et revenir au début
              </button>
            )}
          </div>
        </div>

        <footer className="mt-5 pt-3 border-t border-rule flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-ink/60">
          <span>2 échecs → 5 min de blocage</span>
          <span>session opaque révocable</span>
          <span>mots de passe PBKDF2-SHA256 (600 000 itérations)</span>
          <span>journal d'audit consultable</span>
        </footer>
      </div>
    </div>
  );
}

/**
 * Premier démarrage : aucun administrateur enrôlé.
 *
 * Le jeton de bootstrap est un secret de déploiement (`BO_BOOTSTRAP_TOKEN`
 * dans `.env`) : sans lui, n'importe qui pourrait s'enrôler comme
 * administrateur en scannant son visage.
 */
function BootstrapPage({ health, onDone, demoOnly }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [cameraOn, setCameraOn] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const videoRef = useRef(null);

  const submit = async (event) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const image = await captureBase64(videoRef.current);
      await api.bootstrap({ username, password, token, image_b64: image });
      onDone?.();
    } catch (apiError) {
      setError(apiError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-page flex items-center justify-center p-6">
      <div className="w-full max-w-3xl bg-card rounded-2xl shadow-xl p-6">
        <h1 className="text-ink text-xl font-bold">Premier démarrage — enrôlement administrateur</h1>
        <p className="text-ink/70 text-sm mt-1 mb-4">
          Aucun administrateur n'a de visage enrôlé. Renseignez le jeton de
          déploiement, votre mot de passe et votre visage : ce compte sera le
          premier administrateur, seul habilité à créer les autres.
        </p>
        {demoOnly && (
          <Alert tone="warn">moteur facial de démonstration (STUB) actif</Alert>
        )}
        <div className="grid md:grid-cols-2 gap-6">
          <div>
            <CameraView
              active={cameraOn}
              videoRef={videoRef}
              onError={(err) => setError(err.message)}
              label="ENRÔLEMENT"
            />
            <button
              onClick={() => setCameraOn((value) => !value)}
              className="w-full mt-2 text-ink/60 text-xs font-bold underline"
            >
              {cameraOn ? "éteindre la caméra" : "rallumer la caméra"}
            </button>
          </div>
          <form onSubmit={submit}>
            {error && <Alert tone="error">{error}</Alert>}
            <Field
              label="Identifiant administrateur"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              placeholder="prenom.nom"
            />
            <Field
              label="Mot de passe (10 caractères minimum)"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
            />
            <Field
              label="Jeton de déploiement"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="BO_BOOTSTRAP_TOKEN"
            />
            <button
              type="submit"
              disabled={busy || !cameraOn}
              className="w-full bg-ink text-white font-bold rounded-lg py-2.5 disabled:opacity-40"
            >
              {busy ? "enrôlement…" : "Enrôler l'administrateur"}
            </button>
            {health?.token_configured === false && (
              <Alert tone="warn">
                Aucun jeton de bootstrap n'est configuré : renseignez
                <code className="mx-1">BO_BOOTSTRAP_TOKEN</code> dans <code>.env</code> puis
                <code className="mx-1">docker compose up -d backoffice</code>.
              </Alert>
            )}
          </form>
        </div>
      </div>
    </div>
  );
}
