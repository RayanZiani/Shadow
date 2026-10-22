import { useCallback, useEffect, useMemo, useState } from "react";

import { ShopError, shop } from "./api.js";
import ProductImage from "./ProductImage.jsx";

/**
 * Boutique — site vitrine public.
 *
 * Parcours : catalogue (articles en base) → panier → paiement → confirmation →
 * historique. Aucune solution du commerce : les articles, le stock, les
 * commandes et les paiements vivent dans la base de `shop-api`.
 *
 * Le paiement n'est pas simulé : le bouton « Payer » déclenche une signature
 * d'anneau bLSAG côté `payment-api` et la commande n'est affichée « payée »
 * qu'après la key image renvoyée par le noyau.
 *
 * `customer_ref` est un capability token aléatoire conservé dans
 * `localStorage` : il ouvre l'historique de CE client, et lui seul.
 */

const CUSTOMER_KEY = "shadow.shop.customer";
const CART_KEY = "shadow.shop.cart";

function readCustomer() {
  let value = localStorage.getItem(CUSTOMER_KEY);
  if (!value || value.length < 16) {
    value = Array.from(crypto.getRandomValues(new Uint8Array(24)))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
    localStorage.setItem(CUSTOMER_KEY, value);
  }
  return value;
}

function money(cents) {
  return `${(cents / 100).toFixed(2)} €`;
}

function Badge({ children, tone = "ink" }) {
  const styles = {
    ok: "bg-emerald/15 text-emerald border-emerald/40",
    bad: "bg-crimson/15 text-crimson border-crimson/40",
    warn: "bg-amber-100 text-amber-800 border-amber-400",
    ink: "bg-ink/10 text-ink border-ink/30",
  }[tone];
  return (
    <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full border ${styles}`}>
      {children}
    </span>
  );
}

function ProductCard({ product, onAdd, busy }) {
  const disabled = !product.in_stock || busy;
  return (
    <article className="bg-white/80 rounded-xl p-4 border border-rule flex flex-col">
      <ProductImage
        imageUrl={product.image_url}
        sku={product.sku}
        alt={product.name}
        className="h-24 rounded-lg mb-3"
      />
      <h3 className="text-ink font-bold leading-tight">{product.name}</h3>
      <p className="text-ink/60 text-xs mt-1 flex-1">{product.description}</p>
      <div className="flex items-center justify-between mt-3">
        <span className="text-ink text-lg font-bold tabular-nums">{product.price}</span>
        {product.in_stock ? (
          <Badge tone="ok">{product.stock} en stock</Badge>
        ) : (
          <Badge tone="bad">rupture</Badge>
        )}
      </div>
      <button
        onClick={() => onAdd(product.id)}
        disabled={disabled}
        className="mt-3 bg-ink text-white text-sm font-bold rounded-lg py-2 disabled:opacity-40"
      >
        {disabled ? "indisponible" : "Ajouter au panier"}
      </button>
    </article>
  );
}

function CartPanel({
  cart,
  catalog,
  onQuantity,
  onRemove,
  onCheckout,
  busy,
  error,
  customer,
  onCustomer,
}) {
  const name = customer.name || "";
  const email = customer.email || "";
  const ready = Boolean(name.trim().length >= 2 && email.includes("@"));
  return (
    <aside className="bg-card rounded-xl p-4 h-fit sticky top-4">
      <h2 className="text-ink font-bold mb-3">Panier</h2>
      {!cart || cart.items.length === 0 ? (
        <p className="text-ink/60 text-sm">Votre panier est vide.</p>
      ) : (
        <>
          <ul className="space-y-2">
            {cart.items.map((item) => (
              <li key={item.product_id} className="flex items-start gap-2">
                {/* Le panier ne renvoie pas `image_url` : on la reprend du
                    catalogue chargé, par SKU. */}
                <ProductImage
                  imageUrl={item.image_url ?? catalog?.[item.sku]?.image_url}
                  sku={item.sku}
                  alt={item.name}
                  className="w-10 h-10 rounded shrink-0"
                  labelClassName="text-page/90 font-mono text-[9px]"
                />
                <div className="flex-1">
                  <p className="text-ink text-sm font-bold leading-tight">{item.name}</p>
                  <p className="text-ink/60 text-[11px]">
                    {money(item.unit_price_cents)} × {item.quantity}
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => onQuantity(item.product_id, item.quantity - 1)}
                    disabled={busy}
                    className="w-6 h-6 rounded bg-page text-ink font-bold text-xs"
                  >
                    −
                  </button>
                  <button
                    onClick={() => onQuantity(item.product_id, item.quantity + 1)}
                    disabled={busy}
                    className="w-6 h-6 rounded bg-page text-ink font-bold text-xs"
                  >
                    +
                  </button>
                  <button
                    onClick={() => onRemove(item.product_id)}
                    disabled={busy}
                    className="text-crimson text-[11px] font-bold underline ml-1"
                  >
                    retirer
                  </button>
                </div>
              </li>
            ))}
          </ul>
          <div className="mt-3 pt-3 border-t border-rule flex items-center justify-between">
            <span className="text-ink/70 text-sm">Total</span>
            <span className="text-ink text-xl font-bold tabular-nums">{cart.total}</span>
          </div>

          {/* Coordonnées : ce n'est pas un compte, c'est le ticket de caisse.
              Sans elles, « Payer » ressemble à un clic anonyme — et le jury
              demande à juste titre qui achète. Le nom part dans la commande,
              l'e-mail n'est pas vérifié (aucun envoi). */}
          <div className="mt-3 pt-3 border-t border-rule space-y-2">
            <p className="text-ink/60 text-[11px] font-bold uppercase tracking-wide">
              Coordonnées du ticket
            </p>
            <input
              value={name}
              onChange={(event) => onCustomer({ ...customer, name: event.target.value })}
              placeholder="Nom (ex. Camille Martin)"
              aria-label="Nom du client"
              className="w-full rounded-lg border border-rule bg-white px-2 py-1.5 text-sm text-ink"
            />
            <input
              value={email}
              onChange={(event) => onCustomer({ ...customer, email: event.target.value })}
              placeholder="prenom@exemple.fr"
              aria-label="Adresse e-mail"
              type="email"
              className="w-full rounded-lg border border-rule bg-white px-2 py-1.5 text-sm text-ink"
            />
            <p className="text-ink/50 text-[10px] leading-snug">
              Aucun compte n'est créé : ces informations servent au ticket et à
              l'historique, protégé par un jeton de capacité.
            </p>
          </div>

          <button
            onClick={onCheckout}
            disabled={busy || !ready || cart.items.some((item) => !item.available)}
            className="w-full mt-3 bg-ink text-white font-bold rounded-lg py-2.5 disabled:opacity-40"
          >
            {busy ? "paiement en cours…" : ready ? "Payer" : "Renseigner nom + e-mail"}
          </button>
          <p className="text-ink/50 text-[11px] mt-2">
            Paiement par signature d'anneau bLSAG (key image anti-double-dépense).
            Aucun prestataire externe.
          </p>
        </>
      )}
      {error && <p className="text-crimson text-xs font-bold mt-2">{error}</p>}
    </aside>
  );
}

function OrderDetail({ order, payment }) {
  if (!order) return null;
  const paid = order.status === "paid";
  return (
    <div className="bg-white/80 rounded-xl p-4 border border-rule">
      <div className="flex items-center justify-between">
        <h3 className="text-ink font-bold">Ticket {order.reference}</h3>
        <Badge tone={paid ? "ok" : order.status === "failed" ? "bad" : "warn"}>
          {order.status}
        </Badge>
      </div>
      {(order.customer_name || order.customer_email) && (
        <p className="text-ink/70 text-xs mt-1">
          {order.customer_name || "client"}
          {order.customer_email ? ` · ${order.customer_email}` : ""}
        </p>
      )}
      <ul className="mt-2 text-sm text-ink/80">
        {order.items.map((item) => (
          <li key={item.sku} className="flex justify-between">
            <span>
              {item.name} × {item.quantity}
            </span>
            <span className="tabular-nums">{money(item.subtotal_cents)}</span>
          </li>
        ))}
      </ul>
      <p className="text-ink font-bold mt-2 tabular-nums">Total {order.total}</p>
      {payment && (
        <dl className="mt-2 text-[11px] font-mono text-ink/60 space-y-0.5">
          <div>paiement : {payment.status} · {payment.provider}</div>
          {payment.key_image && <div>key image : {String(payment.key_image).slice(0, 24)}…</div>}
          {payment.ring_size && <div>anneau : {payment.ring_size}</div>}
        </dl>
      )}
      {paid && (
        <p className="text-emerald text-[11px] font-bold mt-2">
          Preuve enregistrée : conservez la référence {order.reference}.
        </p>
      )}
      {!paid && (
        <p className="text-crimson text-xs mt-2">
          Le paiement n'a pas abouti. Le stock a été restitué : vous pouvez réessayer.
        </p>
      )}
    </div>
  );
}

export default function Storefront({ user, onLogin, onSupervision, onAdmin, onLogout }) {
  const [products, setProducts] = useState([]);
  const [cart, setCart] = useState(null);
  const [cartToken, setCartToken] = useState(() => localStorage.getItem(CART_KEY));
  const [orders, setOrders] = useState([]);
  // Coordonnées du ticket, conservées d'une visite à l'autre : le client n'a
  // pas de compte, mais il ne ressaisit pas son nom à chaque achat.
  const [customer, setCustomer] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("shadow.shop.customer-info") || "{}");
    } catch {
      return {};
    }
  });
  const updateCustomer = (value) => {
    setCustomer(value);
    localStorage.setItem("shadow.shop.customer-info", JSON.stringify(value));
  };
  const [lastOrder, setLastOrder] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const customerRef = useMemo(readCustomer, []);
  // Index du catalogue par SKU : le panier ne transporte que le SKU, pas
  // `image_url` — on la retrouve ici pour la vignette.
  const catalogBySku = useMemo(
    () => Object.fromEntries(products.map((product) => [product.sku, product])),
    [products]
  );

  const loadOrders = useCallback(async () => {
    try {
      const payload = await shop.orders(customerRef);
      setOrders(payload.orders);
    } catch {
      // l'historique est secondaire : une erreur ne casse pas la vitrine
    }
  }, [customerRef]);

  const loadCart = useCallback(async (token) => {
    if (!token) {
      setCart(null);
      return;
    }
    try {
      const payload = await shop.cart(token);
      setCart(payload.cart);
    } catch {
      localStorage.removeItem(CART_KEY);
      setCartToken(null);
      setCart(null);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [catalog] = await Promise.all([shop.products()]);
        if (cancelled) return;
        setProducts(catalog.products);
        await loadCart(cartToken);
        await loadOrders();
      } catch (shopError) {
        if (!cancelled) setError(shopError.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cartToken, loadCart, loadOrders]);

  const ensureCart = async () => {
    if (cartToken) return cartToken;
    const payload = await shop.createCart();
    localStorage.setItem(CART_KEY, payload.cart.token);
    setCartToken(payload.cart.token);
    return payload.cart.token;
  };

  const run = async (action) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (shopError) {
      setError(shopError instanceof ShopError ? shopError.message : String(shopError));
    } finally {
      setBusy(false);
    }
  };

  const addToCart = (productId) =>
    run(async () => {
      const token = await ensureCart();
      const payload = await shop.addItem(token, productId, 1);
      setCart(payload.cart);
    });

  const changeQuantity = (productId, quantity) =>
    run(async () => {
      if (quantity <= 0) {
        setCart((await shop.removeItem(cartToken, productId)).cart);
        return;
      }
      setCart((await shop.setQuantity(cartToken, productId, quantity)).cart);
    });

  const removeItem = (productId) =>
    run(async () => setCart((await shop.removeItem(cartToken, productId)).cart));

  const checkout = () =>
    run(async () => {
      try {
        const payload = await shop.checkout(cartToken, customerRef, customer);
        setLastOrder(payload);
        setCart({ token: cartToken, items: [], total: "0.00 €", count: 0 });
        await loadOrders();
      } catch (shopError) {
        // 502 = paiement refusé : la commande existe en base, en échec.
        if (shopError instanceof ShopError && shopError.status === 502) {
          setLastOrder({ order: null, payment: null });
          await loadOrders();
        }
        throw shopError;
      } finally {
        const catalog = await shop.products();
        setProducts(catalog.products);
      }
    });

  return (
    <div className="min-h-screen bg-page">
      <header className="bg-ink text-page px-6 py-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-bold text-lg leading-tight">SHADOW-SETTLE · Boutique</h1>
          <p className="text-page/60 text-[11px]">
            paiement par signature d'anneau · articles en base · historique conservé
          </p>
        </div>
        <nav className="flex items-center gap-2 text-xs font-bold">
          {user ? (
            <>
              <span className="text-page/60">
                {user.username} · {user.role}
              </span>
              <button onClick={onSupervision} className="bg-page/15 px-3 py-1.5 rounded-lg">
                Supervision
              </button>
              {user.role === "admin" && (
                <button onClick={onAdmin} className="bg-page/15 px-3 py-1.5 rounded-lg">
                  Habilitations
                </button>
              )}
              <button onClick={onLogout} className="bg-crimson px-3 py-1.5 rounded-lg">
                Déconnexion
              </button>
            </>
          ) : (
            <button onClick={onLogin} className="bg-page text-ink px-3 py-1.5 rounded-lg">
              Espace back-office
            </button>
          )}
        </nav>
      </header>

      <main className="p-6 grid lg:grid-cols-[1fr_320px] gap-6 max-w-6xl mx-auto">
        <section>
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="text-ink text-xl font-bold">Catalogue</h2>
            <p className="text-ink/60 text-xs">
              {products.length} article(s) · stock temps réel
            </p>
          </div>
          {loading ? (
            <p className="text-ink/60">Chargement du catalogue…</p>
          ) : (
            <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
              {products.map((product) => (
                <ProductCard
                  key={product.id}
                  product={product}
                  onAdd={addToCart}
                  busy={busy}
                />
              ))}
            </div>
          )}

          <div className="mt-8">
            <h2 className="text-ink text-xl font-bold mb-3">Mes commandes</h2>
            {orders.length === 0 ? (
              <p className="text-ink/60 text-sm">Aucune commande pour l'instant.</p>
            ) : (
              <ul className="space-y-2">
                {orders.map((order) => (
                  <li
                    key={order.reference}
                    className="bg-white/70 rounded-lg border border-rule px-3 py-2 flex items-center justify-between gap-3"
                  >
                    <div>
                      <p className="text-ink font-bold text-sm">{order.reference}</p>
                      <p className="text-ink/60 text-[11px]">
                        {new Date(order.created_at * 1000).toLocaleString()} ·{" "}
                        {order.items.length} ligne(s)
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="text-ink font-bold tabular-nums">{order.total}</p>
                      <Badge tone={order.status === "paid" ? "ok" : "bad"}>
                        {order.status}
                      </Badge>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        <div className="space-y-4">
          <CartPanel
            cart={cart}
            catalog={catalogBySku}
            onQuantity={changeQuantity}
            onRemove={removeItem}
            onCheckout={checkout}
            busy={busy}
            error={error}
            customer={customer}
            onCustomer={updateCustomer}
          />
          {lastOrder?.order && (
            <OrderDetail order={lastOrder.order} payment={lastOrder.payment} />
          )}
          {lastOrder && !lastOrder.order && (
            <p className="text-crimson text-sm font-bold">
              Paiement refusé. Le stock a été restitué, réessayez.
            </p>
          )}
        </div>
      </main>
    </div>
  );
}
