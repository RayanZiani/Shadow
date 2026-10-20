import { useEffect, useRef, useState } from "react";

import { CameraError, closeCamera, openCamera } from "./camera.js";

/**
 * Aperçu caméra piloté par l'état du parent.
 *
 * Le flux est ouvert quand `active` devient vrai et **systématiquement fermé**
 * quand il repasse à faux (ou au démontage). C'est ce qui « éteint la caméra »
 * au sens de la consigne : la LED de la webcam s'éteint vraiment, ce n'est pas
 * un simple `display: none`.
 *
 * `videoRef` est fourni par le parent : c'est lui qui capture l'image au
 * moment du scan (`captureBase64`), pas ce composant.
 */
export default function CameraView({ active, videoRef, onError, label }) {
  const localRef = useRef(null);
  const ref = videoRef || localRef;
  const streamRef = useRef(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const start = async () => {
      if (!active) return;
      try {
        const stream = await openCamera(ref.current);
        if (cancelled) {
          closeCamera(stream);
          return;
        }
        streamRef.current = stream;
        setReady(true);
      } catch (error) {
        if (cancelled) return;
        setReady(false);
        onError?.(
          error instanceof CameraError
            ? error
            : new CameraError("unknown", "caméra indisponible")
        );
      }
    };

    if (active) {
      start();
    } else {
      closeCamera(streamRef.current);
      streamRef.current = null;
      setReady(false);
    }

    return () => {
      cancelled = true;
      closeCamera(streamRef.current);
      streamRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  return (
    <div className="relative rounded-xl overflow-hidden bg-ink/90 aspect-[4/3] w-full max-w-md mx-auto">
      <video
        ref={ref}
        className="w-full h-full object-cover"
        style={{ display: active && ready ? "block" : "none" }}
        aria-label="Aperçu de la caméra USB"
      />
      {active && !ready && (
        <div className="absolute inset-0 flex items-center justify-center text-page text-sm font-bold">
          ACTIVATION DE LA CAMÉRA…
        </div>
      )}
      {!active && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-page/70">
          <span className="text-4xl">◉</span>
          <span className="text-xs font-bold uppercase tracking-widest">
            caméra éteinte
          </span>
        </div>
      )}
      {active && ready && label && (
        <span className="absolute top-2 left-2 bg-crimson text-white text-[11px] font-bold px-2 py-0.5 rounded-full">
          {label}
        </span>
      )}
    </div>
  );
}
