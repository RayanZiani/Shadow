import { useEffect, useState } from "react";

import { productImageUrl } from "./api.js";

/**
 * Image produit avec repli visuel.
 *
 * `shop-api` expose `image_url` en chemin relatif (`/static/products/TERM-01.svg`) :
 * l'URL absolue est construite dans `api.js` à partir de `SHOP_BASE`. Si le
 * fichier manque — déploiement sans dossier `static`, article créé à la main
 * sans image — ou si le chargement échoue, on retombe sur la vignette
 * typographique au SKU : jamais une image cassée, jamais de cadre vide.
 *
 * `loading="lazy"` : la vitrine affiche six articles, mais le catalogue peut
 * grandir ; les images hors écran ne sont pas téléchargées.
 */
export default function ProductImage({
  imageUrl,
  sku,
  alt,
  className = "",
  labelClassName = "text-page/90 font-mono text-xs tracking-widest",
}) {
  const [failed, setFailed] = useState(false);
  const src = productImageUrl(imageUrl);

  // Un article réaffiché avec une nouvelle image doit retenter le chargement.
  useEffect(() => setFailed(false), [src]);

  return (
    <div
      className={`bg-gradient-to-br from-ink/80 to-ink/40 flex items-center justify-center overflow-hidden ${className}`}
      role={src && !failed ? undefined : "img"}
      aria-label={src && !failed ? undefined : alt || sku || "image produit indisponible"}
    >
      {src && !failed ? (
        <img
          src={src}
          alt={alt || sku || "image produit"}
          loading="lazy"
          onError={() => setFailed(true)}
          className="h-full w-full object-contain"
        />
      ) : (
        <span className={`${labelClassName} text-center px-1`}>{sku || "—"}</span>
      )}
    </div>
  );
}
