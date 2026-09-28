import { type FormEvent, type ReactNode, useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../lib/api";
import type { Role } from "../lib/types";
import { Mark } from "./Brand";

interface Props {
  role: Role;
  title: string;
  /** Why this view is gated, in one sentence: shown on the sign-in form. */
  blurb: string;
  children: (session: { logout: () => void; expired: () => void }) => ReactNode;
}

/** A role's sign-in, in front of its view.
 *
 *  Nothing of the view renders until the server says this browser is signed
 *  in as this role. The data was always behind the check; the page is too, so
 *  nobody is shown an empty queue or an empty dashboard and left to wonder
 *  whether it is empty. `expired` is for the view to call on a 401 mid-way:
 *  the eight-hour session ran out, back to the form. */
export function Gate({ role, title, blurb, children }: Props) {
  const [auth, setAuth] = useState<"checking" | "out" | "in">("checking");
  const [unavailable, setUnavailable] = useState<string | null>(null);

  useEffect(() => {
    api
      .roleSession(role)
      .then(() => setAuth("in"))
      .catch((e: ApiError) => {
        if (e.status !== 401) setUnavailable(e.message);
        setAuth("out");
      });
  }, [role]);

  const expired = useCallback(() => setAuth("out"), []);
  const logout = useCallback(() => {
    api.roleLogout(role).finally(() => setAuth("out"));
  }, [role]);

  if (auth === "checking") return null;
  if (auth === "out") {
    return (
      <Login
        role={role}
        title={title}
        blurb={blurb}
        unavailable={unavailable}
        onSignedIn={() => setAuth("in")}
      />
    );
  }
  return <>{children({ logout, expired })}</>;
}

function Login({
  role,
  title,
  blurb,
  unavailable,
  onSignedIn,
}: {
  role: Role;
  title: string;
  blurb: string;
  unavailable: string | null;
  onSignedIn: () => void;
}) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    api
      .roleLogin(role, password)
      .then(onSignedIn)
      .catch((e: ApiError) => setError(e.status === 401 ? "Wrong password." : e.message))
      .finally(() => setBusy(false));
  }

  return (
    <div className="login">
      <a className="back" href="/">← back to the conversation</a>
      <form onSubmit={submit}>
        <div className="brand">
          <Mark />
          <span className="name">Fibrapunto</span>
        </div>
        <h1>{title}</h1>
        <p className="muted">{blurb}</p>
        {unavailable ? (
          <p className="login-error">{unavailable}</p>
        ) : (
          <>
            <label htmlFor={`${role}-password`}>Password</label>
            <input
              id={`${role}-password`}
              type="password"
              autoComplete="current-password"
              autoFocus
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            {error && <p className="login-error">{error}</p>}
            <button className="primary" type="submit" disabled={busy || !password}>
              Sign in
            </button>
          </>
        )}
      </form>
    </div>
  );
}
