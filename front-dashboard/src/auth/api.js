/**
 * Client HTTP du back-office.
 *
 * Un seul endroit qui connaît l'URL et la forme des erreurs. Le back-office
 * renvoie toujours `detail = { reason, message, ... }` : l'interface peut donc
 * réagir à `locked` / `retry_after` sans analyser une chaîne de caractères.
 */
import { readToken } from "./session.js";

const BO_BASE = import.meta.env.VITE_BACKOFFICE_BASE || "http://localhost:8086";

export { BO_BASE };

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail?.message || `erreur ${status}`);
    this.status = status;
    this.detail = detail || {};
    this.reason = detail?.reason || "unknown";
  }
}

async function request(path, { method = "GET", body, token } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const bearer = token ?? readToken();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;

  let response;
  try {
    response = await fetch(`${BO_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, {
      reason: "unreachable",
      message: "back-office injoignable sur le PC hôte",
    });
  }

  if (response.status === 204) return null;

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload?.detail;
    throw new ApiError(
      response.status,
      typeof detail === "object" && detail !== null
        ? detail
        : { reason: "error", message: typeof detail === "string" ? detail : `erreur ${response.status}` }
    );
  }
  return payload;
}

export const api = {
  health: () => request("/healthz"),
  bootstrapStatus: () => request("/auth/bootstrap/status"),
  bootstrap: (payload) => request("/auth/bootstrap", { method: "POST", body: payload }),
  identifyStart: (username) =>
    request("/auth/identify/start", { method: "POST", body: { username } }),
  identify: (challengeId, imageB64) =>
    request("/auth/identify", {
      method: "POST",
      body: { challenge_id: challengeId, image_b64: imageB64 },
    }),
  password: (challengeId, password) =>
    request("/auth/password", {
      method: "POST",
      body: { challenge_id: challengeId, password },
    }),
  me: () => request("/auth/me"),
  logout: () => request("/auth/logout", { method: "POST" }),

  listUsers: () => request("/admin/users"),
  createUser: (payload) => request("/admin/users", { method: "POST", body: payload }),
  updateUser: (id, payload) =>
    request(`/admin/users/${id}`, { method: "PATCH", body: payload }),
  deactivateUser: (id) => request(`/admin/users/${id}`, { method: "DELETE" }),
  enrollFace: (id, imageB64) =>
    request(`/admin/users/${id}/face`, { method: "POST", body: { image_b64: imageB64 } }),
  removeFace: (id) => request(`/admin/users/${id}/face`, { method: "DELETE" }),
  unlockUser: (id) => request(`/admin/users/${id}/unlock`, { method: "POST" }),
  audit: (limit = 100) => request(`/admin/audit?limit=${limit}`),
  sessions: () => request("/admin/sessions"),
  revokeSession: (id) => request(`/admin/sessions/${id}`, { method: "DELETE" }),
  stats: () => request("/admin/stats"),
};
