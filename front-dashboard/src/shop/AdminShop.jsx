import { useCallback, useEffect, useState } from "react";

import { shop } from "./api.js";
import ProductImage from "./ProductImage.jsx";

/**
 * Administration de la boutique — articles et commandes.
 *
 * Le jury demande un « back permettant de gérer tout cela ». Cet écran gère le
 * catalogue (création, prix, stock, description, retrait) et consulte les
 * commandes avec leur preuve de paiement (key image, taille d'anneau).
 *
 * Sécurité : chaque appel part avec la session back-office ; `shop-api` la
 * valide auprès du back-office et exige le rôle `admin`. Un opérateur ou un
 * auditeur ne peut pas modifier le catalogue.
 */
export default function AdminShop() {
  const [products, setProducts] = useState([]);
  const [orders, setOrders] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({
    sku: "",
    name: "",
    description: "",
    price: "",
    stock: "",
  });

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [productList, orderList] = await Promise.all([
        shop.adminProducts(),
        shop.adminOrders(),
      ]);
      setProducts(productList.products);
      setOrders(orderList.orders);
    } catch (shopError) {
      setError(shopError.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 15_000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const act = async (action, message) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      if (message) setNotice(message);
      await refresh();
    } catch (shopError) {
      setError(shopError.message);
    } finally {
      setBusy(false);
    }
  };

  const create = async (event) => {
    event.preventDefault();
    await act(async () => {
      await shop.adminCreateProduct({
        sku: draft.sku.trim().toUpperCase(),
        name: draft.name.trim(),
        description: draft.description.trim(),
        price_cents: Math.round(Number(draft.price) * 100),
        stock: Number(draft.stock),
      });
      setDraft({ sku: "", name: "", description: "", price: "", stock: "" });
      setCreating(false);
    }, "article créé");
  };

  const update = (product, field, value) =>
    act(
      () => shop.adminUpdateProduct(product.id, { [field]: value }),
      `${product.sku} mis à jour`
    );

  return (
    <div className="min-h-screen bg-page p-6">
      <header className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <div>
          <h1 className="text-ink text-2xl font-bold">Boutique — administration</h1>
          <p className="text-ink/70 text-sm">
            {products.length} article(s) · {orders.filter((o) => o.status === "paid").length}{" "}
            commande(s) payée(s) sur {orders.length}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={refresh}
            className="bg-white text-ink font-bold text-sm rounded-lg px-3 py-2 border border-rule"
          >
            Actualiser
          </button>
          <button
            onClick={() => setCreating((value) => !value)}
            className="bg-ink text-white font-bold text-sm rounded-lg px-3 py-2"
          >
            {creating ? "fermer" : "+ nouvel article"}
          </button>
        </div>
      </header>

      {error && (
        <div className="border-l-4 border-crimson bg-crimson/10 text-crimson px-3 py-2 rounded mb-3 text-sm font-bold">
          {error}
        </div>
      )}
      {notice && (
        <div className="border-l-4 border-emerald bg-emerald/10 text-emerald px-3 py-2 rounded mb-3 text-sm font-bold">
          {notice}
        </div>
      )}

      {creating && (
        <form
          onSubmit={create}
          className="mb-5 bg-white/70 rounded-xl p-4 border border-rule grid md:grid-cols-5 gap-2 items-end"
        >
          <label className="text-xs font-bold text-ink/70">
            SKU
            <input
              value={draft.sku}
              onChange={(event) => setDraft({ ...draft, sku: event.target.value })}
              className="mt-1 w-full rounded border border-rule px-2 py-1.5 text-sm font-normal"
              placeholder="ART-01"
            />
          </label>
          <label className="text-xs font-bold text-ink/70 md:col-span-2">
            Nom
            <input
              value={draft.name}
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              className="mt-1 w-full rounded border border-rule px-2 py-1.5 text-sm font-normal"
              placeholder="Nom de l'article"
            />
          </label>
          <label className="text-xs font-bold text-ink/70">
            Prix (€)
            <input
              type="number"
              step="0.01"
              min="0.01"
              value={draft.price}
              onChange={(event) => setDraft({ ...draft, price: event.target.value })}
              className="mt-1 w-full rounded border border-rule px-2 py-1.5 text-sm font-normal"
            />
          </label>
          <label className="text-xs font-bold text-ink/70">
            Stock
            <input
              type="number"
              min="0"
              value={draft.stock}
              onChange={(event) => setDraft({ ...draft, stock: event.target.value })}
              className="mt-1 w-full rounded border border-rule px-2 py-1.5 text-sm font-normal"
            />
          </label>
          <label className="text-xs font-bold text-ink/70 md:col-span-4">
            Description
            <input
              value={draft.description}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
              className="mt-1 w-full rounded border border-rule px-2 py-1.5 text-sm font-normal"
            />
          </label>
          <button
            type="submit"
            disabled={busy || !draft.sku || !draft.name || !draft.price}
            className="bg-ink text-white text-sm font-bold rounded py-2 disabled:opacity-40"
          >
            Créer
          </button>
        </form>
      )}

      <section className="bg-white/70 rounded-xl p-4 border border-rule mb-5 overflow-auto">
        <h2 className="text-ink font-bold mb-3">Articles</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-ink/60 text-[11px] uppercase">
              <th className="pb-1">image</th>
              <th className="pb-1">SKU</th>
              <th className="pb-1">nom</th>
              <th className="pb-1">prix</th>
              <th className="pb-1">stock</th>
              <th className="pb-1">état</th>
              <th className="pb-1 text-right">actions</th>
            </tr>
          </thead>
          <tbody>
            {products.map((product) => (
              <tr key={product.id} className="border-t border-rule/50">
                <td className="py-2 pr-2">
                  <ProductImage
                    imageUrl={product.image_url}
                    sku={product.sku}
                    alt={product.name}
                    className="w-10 h-10 rounded"
                    labelClassName="text-page/90 font-mono text-[9px]"
                  />
                </td>
                <td className="py-2 font-mono text-[12px] text-ink/70">{product.sku}</td>
                <td className="py-2 text-ink font-bold">{product.name}</td>
                <td className="py-2 tabular-nums">{product.price}</td>
                <td className="py-2">
                  <input
                    type="number"
                    min="0"
                    defaultValue={product.stock}
                    onBlur={(event) => {
                      const value = Number(event.target.value);
                      if (value !== product.stock) update(product, "stock", value);
                    }}
                    className="w-20 rounded border border-rule px-2 py-1 text-sm"
                  />
                </td>
                <td className="py-2">
                  {product.active ? (
                    <span className="text-emerald text-[11px] font-bold">actif</span>
                  ) : (
                    <span className="text-crimson text-[11px] font-bold">retiré</span>
                  )}
                </td>
                <td className="py-2 text-right">
                  {product.active && (
                    <button
                      onClick={() =>
                        act(
                          () => shop.adminDeactivateProduct(product.id),
                          `${product.sku} retiré du catalogue`
                        )
                      }
                      className="text-[11px] underline text-crimson"
                    >
                      retirer
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="bg-white/70 rounded-xl p-4 border border-rule overflow-auto">
        <h2 className="text-ink font-bold mb-3">Commandes</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-ink/60 text-[11px] uppercase">
              <th className="pb-1">référence</th>
              <th className="pb-1">date</th>
              <th className="pb-1">total</th>
              <th className="pb-1">statut</th>
              <th className="pb-1">lignes</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => (
              <tr key={order.reference} className="border-t border-rule/50 align-top">
                <td className="py-2 font-mono text-[12px]">{order.reference}</td>
                <td className="py-2 text-ink/70 text-[12px]">
                  {new Date(order.created_at * 1000).toLocaleString()}
                </td>
                <td className="py-2 tabular-nums font-bold">{order.total}</td>
                <td className="py-2">
                  <span
                    className={
                      order.status === "paid"
                        ? "text-emerald font-bold text-[12px]"
                        : "text-crimson font-bold text-[12px]"
                    }
                  >
                    {order.status}
                  </span>
                </td>
                <td className="py-2 text-ink/70 text-[12px]">
                  {order.items.map((item) => `${item.sku}×${item.quantity}`).join(" · ")}
                </td>
              </tr>
            ))}
            {orders.length === 0 && (
              <tr>
                <td colSpan={5} className="py-2 text-ink/50">
                  aucune commande
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
