/**
 * Client de la boutique.
 *
 * Le catalogue et le panier sont publics (site vitrine). Seules les routes
 * `/admin/*` exigent une session back-office : le jeton est alors celui de la
 * session opaque, jamais le jeton de service (qui ne quitte pas le serveur).
 */
import { readToken } from "../auth/session.js";

const SHOP_BASE = import.meta.env.VITE_SHOP_BASE || "http://localhost:8087";

export { SHOP_BASE };

export class ShopError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : detail?.detail || `erreur ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, { method = "GET", body, admin = false } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (admin) {
    const token = readToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  let response;
  try {
    response = await fetch(`${SHOP_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ShopError(0, "boutique injoignable — vérifiez que shop-api tourne");
  }
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new ShopError(response.status, payload?.detail ?? `erreur ${response.status}`);
  }
  return payload;
}

export const shop = {
  health: () => request("/healthz"),
  products: () => request("/products"),
  createCart: () => request("/cart", { method: "POST" }),
  cart: (token) => request(`/cart/${token}`),
  addItem: (token, productId, quantity = 1) =>
    request(`/cart/${token}/items`, {
      method: "POST",
      body: { product_id: productId, quantity },
    }),
  setQuantity: (token, productId, quantity) =>
    request(`/cart/${token}/items/${productId}`, {
      method: "PATCH",
      body: { quantity },
    }),
  removeItem: (token, productId) =>
    request(`/cart/${token}/items/${productId}`, { method: "DELETE" }),
  checkout: (token, customerRef) =>
    request("/checkout", {
      method: "POST",
      body: { cart_token: token, customer_ref: customerRef || null },
    }),
  orders: (customerRef) =>
    request(`/orders?customer_ref=${encodeURIComponent(customerRef)}`),
  order: (reference, customerRef) =>
    request(
      `/orders/${encodeURIComponent(reference)}?customer_ref=${encodeURIComponent(customerRef)}`
    ),

  // Administration — session back-office, rôle admin.
  adminProducts: () => request("/admin/products", { admin: true }),
  adminCreateProduct: (payload) =>
    request("/admin/products", { method: "POST", body: payload, admin: true }),
  adminUpdateProduct: (id, payload) =>
    request(`/admin/products/${id}`, { method: "PATCH", body: payload, admin: true }),
  adminDeactivateProduct: (id) =>
    request(`/admin/products/${id}`, { method: "DELETE", admin: true }),
  adminOrders: () => request("/admin/orders", { admin: true }),
};
