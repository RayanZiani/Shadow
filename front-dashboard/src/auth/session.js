/**
 * Session du back-office.
 *
 * Le jeton est opaque (32 octets aléatoires côté serveur) et vit dans
 * `sessionStorage`, pas `localStorage` : fermer l'onglet ferme la session, et
 * un autre onglet ne l'hérite pas. Ce n'est pas un JWT : le serveur peut le
 * révoquer immédiatement (déconnexion, désactivation du compte).
 */
const TOKEN_KEY = "shadow.backoffice.token";
const USER_KEY = "shadow.backoffice.user";

export function saveSession(token, user) {
  sessionStorage.setItem(TOKEN_KEY, token);
  sessionStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function readToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function readUser() {
  try {
    const raw = sessionStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function clearSession() {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USER_KEY);
}
