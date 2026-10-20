/**
 * Accès caméra USB.
 *
 * La capture se fait dans le NAVIGATEUR du PC hôte : c'est le seul endroit où
 * `getUserMedia` a accès à la webcam, et l'image ne sort jamais du réseau
 * local (elle est envoyée en base64 au back-office, sur le même sous-réseau du
 * point d'accès). Aucun flux vidéo n'est transmis : une seule image JPEG par
 * tentative, redimensionnée à 640 px de large.
 *
 * Détails qui évitent des pannes en démonstration :
 *  - `facingMode: "user"` préfère la webcam frontale, mais on n'exige pas une
 *    caméra précise : n'importe quelle caméra USB branchée sur le PC hôte
 *    convient ;
 *  - le flux est arrêté (`track.stop()`) dès la capture ou la fermeture, ce qui
 *    éteint la LED physique de la caméra — c'est l'« extinction » exigée par
 *    la consigne ;
 *  - les erreurs sont traduites en messages actionnables (caméra occupée par
 *    une autre application, HTTPS absent, matériel absent).
 */
const WIDTH = 640;
const JPEG_QUALITY = 0.85;

export class CameraError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export async function openCamera(videoElement) {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new CameraError(
      "insecure_context",
      "caméra indisponible : ouvrez le back-office en HTTPS ou sur localhost"
    );
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: {
        width: { ideal: WIDTH },
        height: { ideal: 480 },
        facingMode: "user",
      },
      audio: false,
    });
  } catch (error) {
    const name = error?.name || "UnknownError";
    if (name === "NotAllowedError" || name === "SecurityError") {
      throw new CameraError("permission_denied", "accès à la caméra refusé");
    }
    if (name === "NotFoundError" || name === "OverconstrainedError") {
      throw new CameraError("no_device", "aucune caméra USB détectée sur le PC hôte");
    }
    if (name === "NotReadableError" || name === "AbortError") {
      throw new CameraError(
        "busy",
        "caméra déjà utilisée par une autre application (Teams, Zoom, navigateur)"
      );
    }
    throw new CameraError("unknown", `caméra indisponible (${name})`);
  }

  if (videoElement) {
    videoElement.srcObject = stream;
    videoElement.muted = true;
    videoElement.playsInline = true;
    try {
      await videoElement.play();
    } catch {
      // Safari peut refuser `play()` sans geste utilisateur : la capture
      // fonctionnera quand même dès que la vidéo a des dimensions.
    }
  }
  return stream;
}

export function closeCamera(stream) {
  if (!stream) return;
  for (const track of stream.getTracks()) {
    try {
      track.stop();
    } catch {
      // un track déjà terminé n'est pas une erreur
    }
  }
}

function waitForDimensions(videoElement, timeoutMs = 2500) {
  return new Promise((resolve) => {
    const started = Date.now();
    const check = () => {
      if (videoElement.videoWidth > 0 && videoElement.videoHeight > 0) {
        resolve(true);
      } else if (Date.now() - started > timeoutMs) {
        resolve(false);
      } else {
        window.setTimeout(check, 60);
      }
    };
    check();
  });
}

/** Capture une image JPEG en base64 (sans préfixe `data:`). */
export async function captureBase64(videoElement) {
  const ready = await waitForDimensions(videoElement);
  if (!ready) {
    throw new CameraError("no_frame", "la caméra ne délivre aucune image");
  }
  const scale = Math.min(1, WIDTH / videoElement.videoWidth);
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(videoElement.videoWidth * scale));
  canvas.height = Math.max(1, Math.round(videoElement.videoHeight * scale));
  const context = canvas.getContext("2d");
  context.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
  const dataUrl = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
  return dataUrl.split(",", 2)[1] || "";
}
