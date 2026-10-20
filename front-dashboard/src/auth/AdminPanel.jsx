import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api.js";
import CameraView from "./CameraView.jsx";
import { captureBase64 } from "./camera.js";

/**
 * Gestion complète des habilitations — exigée par la consigne
 * (« gestion de cette reconnaissance doit être intégrée au back-office,
 * gestion complète des habilitations »).
 *
 * Quatre écrans dans un seul panneau :
 *  1. utilisateurs : créer, changer de rôle, désactiver, débloquer ;
 *  2. visages : enrôler / retirer, depuis la caméra USB de l'hôte ;
 *  3. sessions : voir et révoquer les sessions ouvertes ;
 *  4. audit : journal horodaté de toutes les tentatives, succès et échecs.
 */

const ROLE_STYLE = {
  admin: "bg-crimson text-white",
  operator: "bg-ink text-white",
  auditor: "bg-emerald text-white",
};

const ROLE_LABEL = {
  admin: "administrateur",
  operator: "opérateur",
  auditor: "auditeur",
};

function Panel({ title, children, actions }) {
  return (
    <section className="bg-white/70 rounded-xl p-4 border border-rule">
      <header className="flex items-center justify-between mb-3 gap-3">
        <h2 className="text-ink font-bold">{title}</h2>
        {actions}
      </header>
      {children}
    </section>
  );
}

function Badge({ children, tone = "ink" }) {
  const styles =
    tone === "ok"
      ? "bg-emerald/15 text-emerald border-emerald/40"
      : tone === "bad"
        ? "bg-crimson/15 text-crimson border-crimson/40"
        : "bg-ink/10 text-ink border-ink/30";
  return (
    <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full border ${styles}`}>
      {children}
    </span>
  );
}

function FaceEnrollment({ target, onDone, onCancel }) {
  const videoRef = useRef(null);
  const [active, setActive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const enroll = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const image = await captureBase64(videoRef.current);
      await api.enrollFace(target.id, image);
      setActive(false);
      onDone?.();
    } catch (apiError) {
      setError(apiError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 p-3 rounded-lg bg-page/80 border border-rule">
      <p className="text-ink text-sm font-bold mb-2">
        Enrôlement du visage — {target.username}
      </p>
      <CameraView
        active={active}
        videoRef={videoRef}
        onError={(err) => setError(err.message)}
        label="ENRÔLEMENT"
      />
      {error && <p className="text-crimson text-xs font-bold mt-2">{error}</p>}
      <div className="flex gap-2 mt-2">
        <button
          onClick={enroll}
          disabled={busy || !active}
          className="flex-1 bg-ink text-white text-sm font-bold rounded py-2 disabled:opacity-40"
        >
          {busy ? "capture…" : "Capturer le visage"}
        </button>
        <button
          onClick={() => {
            setActive(false);
            onCancel?.();
          }}
          className="px-3 bg-white text-ink text-sm font-bold rounded py-2"
        >
          Annuler
        </button>
      </div>
    </div>
  );
}

export default function AdminPanel({ currentUser, onLogout, onOpenShop }) {
  const [users, setUsers] = useState([]);
  const [audit, setAudit] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newUser, setNewUser] = useState({ username: "", password: "", role: "operator" });
  const [enrolling, setEnrolling] = useState(null);
  const isAdmin = currentUser?.role === "admin";

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [userList, auditList] = await Promise.all([api.listUsers(), api.audit(80)]);
      setUsers(userList.users);
      setAudit(auditList.events);
      if (isAdmin) {
        const [sessionList, statBlock] = await Promise.all([
          api.sessions(),
          api.stats(),
        ]);
        setSessions(sessionList.sessions);
        setStats(statBlock);
      }
    } catch (apiError) {
      setError(apiError.message);
    }
  }, [isAdmin]);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 10_000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const act = async (fn, successMessage) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (successMessage) setNotice(successMessage);
      await refresh();
    } catch (apiError) {
      setError(apiError.message);
    } finally {
      setBusy(false);
    }
  };

  const createUser = async (event) => {
    event.preventDefault();
    await act(async () => {
      await api.createUser(newUser);
      setNewUser({ username: "", password: "", role: "operator" });
      setCreating(false);
    }, `utilisateur ${newUser.username} créé — enrôlez maintenant son visage`);
  };

  return (
    <div className="min-h-screen bg-page p-6">
      <header className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <div>
          <h1 className="text-ink text-2xl font-bold">Habilitations — back-office</h1>
          <p className="text-ink/70 text-sm">
            connecté en tant que <b>{currentUser?.username}</b> ({ROLE_LABEL[currentUser?.role]})
            {stats && (
              <>
                {" "}· {stats.users_active}/{stats.users_total} comptes actifs ·{" "}
                {stats.faces_enrolled} visages enrôlés · {stats.sessions_active} sessions
                {stats.lockouts_active > 0 && (
                  <span className="text-crimson font-bold">
                    {" "}· {stats.lockouts_active} compte(s) bloqué(s)
                  </span>
                )}
              </>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          {onOpenShop && (
            <button
              onClick={onOpenShop}
              className="bg-white text-ink font-bold text-sm rounded-lg px-3 py-2 border border-rule"
            >
              Boutique
            </button>
          )}
          <button
            onClick={refresh}
            className="bg-white text-ink font-bold text-sm rounded-lg px-3 py-2 border border-rule"
          >
            Actualiser
          </button>
          <button
            onClick={onLogout}
            className="bg-ink text-white font-bold text-sm rounded-lg px-3 py-2"
          >
            Se déconnecter
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

      <div className="grid lg:grid-cols-2 gap-5">
        <Panel
          title="Utilisateurs et rôles"
          actions={
            isAdmin && (
              <button
                onClick={() => setCreating((value) => !value)}
                className="bg-ink text-white text-xs font-bold rounded px-3 py-1.5"
              >
                {creating ? "fermer" : "+ nouvel utilisateur"}
              </button>
            )
          }
        >
          {creating && (
            <form onSubmit={createUser} className="mb-3 p-3 rounded-lg bg-page/80 border border-rule">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                <input
                  value={newUser.username}
                  onChange={(event) =>
                    setNewUser({ ...newUser, username: event.target.value })
                  }
                  placeholder="identifiant"
                  className="rounded border border-rule px-2 py-1.5 text-sm"
                  autoComplete="off"
                />
                <input
                  type="password"
                  value={newUser.password}
                  onChange={(event) =>
                    setNewUser({ ...newUser, password: event.target.value })
                  }
                  placeholder="mot de passe (10+)"
                  className="rounded border border-rule px-2 py-1.5 text-sm"
                  autoComplete="new-password"
                />
                <select
                  value={newUser.role}
                  onChange={(event) => setNewUser({ ...newUser, role: event.target.value })}
                  className="rounded border border-rule px-2 py-1.5 text-sm bg-white"
                >
                  {Object.entries(ROLE_LABEL).map(([value, text]) => (
                    <option key={value} value={value}>
                      {text}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="submit"
                disabled={busy || !newUser.username || newUser.password.length < 10}
                className="w-full mt-2 bg-ink text-white text-sm font-bold rounded py-1.5 disabled:opacity-40"
              >
                Créer le compte
              </button>
              <p className="text-ink/60 text-[11px] mt-1">
                Un compte sans visage enrôlé ne peut pas se connecter : c'est un
                garde-fou volontaire.
              </p>
            </form>
          )}

          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-ink/60 text-[11px] uppercase">
                <th className="pb-1">identifiant</th>
                <th className="pb-1">rôle</th>
                <th className="pb-1">état</th>
                <th className="pb-1 text-right">actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id} className="border-t border-rule/50 align-top">
                  <td className="py-2 text-ink font-bold">
                    {user.username}
                    {user.id === currentUser?.id && (
                      <span className="text-ink/50 text-[11px]"> (vous)</span>
                    )}
                    <div className="text-ink/50 text-[11px] font-normal">
                      {user.face_enrolled ? "visage enrôlé" : "sans visage"}
                      {user.last_login_at && (
                        <> · {new Date(user.last_login_at * 1000).toLocaleString()}</>
                      )}
                    </div>
                  </td>
                  <td className="py-2">
                    <span
                      className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${ROLE_STYLE[user.role]}`}
                    >
                      {ROLE_LABEL[user.role]}
                    </span>
                  </td>
                  <td className="py-2 space-y-1">
                    {!user.active && <Badge tone="bad">désactivé</Badge>}
                    {user.locked && (
                      <Badge tone="bad">bloqué {user.retry_after}s</Badge>
                    )}
                    {user.active && !user.locked && <Badge tone="ok">actif</Badge>}
                  </td>
                  <td className="py-2 text-right space-x-1 whitespace-nowrap">
                    {isAdmin && (
                      <>
                        <button
                          onClick={() => setEnrolling(user)}
                          className="text-[11px] font-bold underline text-ink"
                        >
                          visage
                        </button>
                        {user.face_enrolled && (
                          <button
                            onClick={() =>
                              act(
                                () => api.removeFace(user.id),
                                `visage retiré de ${user.username}`
                              )
                            }
                            className="text-[11px] underline text-crimson"
                          >
                            retirer
                          </button>
                        )}
                        {user.locked && (
                          <button
                            onClick={() =>
                              act(() => api.unlockUser(user.id), `${user.username} débloqué`)
                            }
                            className="text-[11px] underline text-emerald"
                          >
                            débloquer
                          </button>
                        )}
                        <select
                          value={user.role}
                          onChange={(event) =>
                            act(
                              () => api.updateUser(user.id, { role: event.target.value }),
                              `rôle de ${user.username} modifié`
                            )
                          }
                          className="text-[11px] border border-rule rounded bg-white px-1 py-0.5"
                        >
                          {Object.entries(ROLE_LABEL).map(([value, text]) => (
                            <option key={value} value={value}>
                              {text}
                            </option>
                          ))}
                        </select>
                        <button
                          onClick={() =>
                            act(
                              () => api.updateUser(user.id, { active: !user.active }),
                              `${user.username} ${user.active ? "désactivé" : "réactivé"}`
                            )
                          }
                          className="text-[11px] underline text-ink"
                        >
                          {user.active ? "désactiver" : "réactiver"}
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {enrolling && (
            <FaceEnrollment
              target={enrolling}
              onDone={() => {
                setEnrolling(null);
                setNotice(`visage enrôlé pour ${enrolling.username}`);
                refresh();
              }}
              onCancel={() => setEnrolling(null)}
            />
          )}
        </Panel>

        <Panel title="Journal d'audit (80 derniers événements)">
          <div className="max-h-[420px] overflow-auto">
            <table className="w-full text-[12px] font-mono">
              <tbody>
                {audit.map((event) => (
                  <tr key={event.id} className="border-t border-rule/40">
                    <td className="py-1 text-ink/50 whitespace-nowrap pr-2">
                      {new Date(event.ts * 1000).toLocaleTimeString()}
                    </td>
                    <td className="py-1 pr-2 text-ink font-bold">{event.username ?? "—"}</td>
                    <td className="py-1 pr-2">
                      <span
                        className={
                          event.event.includes("fail") || event.event === "locked"
                            ? "text-crimson font-bold"
                            : event.event.includes("ok") || event.event === "bootstrap"
                              ? "text-emerald font-bold"
                              : "text-ink/80"
                        }
                      >
                        {event.event}
                      </span>
                    </td>
                    <td className="py-1 text-ink/50 whitespace-nowrap">{event.ip}</td>
                  </tr>
                ))}
                {audit.length === 0 && (
                  <tr>
                    <td className="py-2 text-ink/50">aucun événement</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        {isAdmin && (
          <Panel title="Sessions ouvertes">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-ink/60 text-[11px] uppercase">
                  <th className="pb-1">utilisateur</th>
                  <th className="pb-1">dernière activité</th>
                  <th className="pb-1">IP</th>
                  <th className="pb-1 text-right">action</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((session) => (
                  <tr key={session.id} className="border-t border-rule/50">
                    <td className="py-2 font-bold text-ink">{session.username}</td>
                    <td className="py-2 text-ink/70 text-[12px]">
                      {new Date(session.last_seen * 1000).toLocaleTimeString()}
                    </td>
                    <td className="py-2 text-ink/60 text-[12px] font-mono">{session.ip}</td>
                    <td className="py-2 text-right">
                      <button
                        onClick={() =>
                          act(
                            () => api.revokeSession(session.id),
                            `session de ${session.username} révoquée`
                          )
                        }
                        className="text-[11px] underline text-crimson"
                      >
                        révoquer
                      </button>
                    </td>
                  </tr>
                ))}
                {sessions.length === 0 && (
                  <tr>
                    <td className="py-2 text-ink/50 text-sm">aucune session active</td>
                  </tr>
                )}
              </tbody>
            </table>
          </Panel>
        )}

        <Panel title="Politique appliquée">
          <ul className="text-sm text-ink/80 space-y-1 list-disc pl-4">
            <li>visage obligatoire avant la saisie du mot de passe ;</li>
            <li>2 échecs (visage ou mot de passe) → blocage 5 minutes, tenu par le serveur ;</li>
            <li>mot de passe : 10 caractères minimum, PBKDF2-SHA256 600 000 itérations ;</li>
            <li>sessions opaques, expirées après 8 h ou 30 min d'inactivité, révocables ;</li>
            <li>toute tentative, réussie ou refusée, est journalisée ;</li>
            <li>le dernier administrateur actif ne peut pas être désactivé.</li>
          </ul>
          <p className="text-ink/50 text-[11px] mt-2">
            Limite assumée : la détection de vivacité (anti-photo) n'est pas
            implémentée et est publiée dans <code>/healthz</code> (« liveness:
            not_implemented »).
          </p>
        </Panel>
      </div>
    </div>
  );
}

export { ROLE_LABEL };
