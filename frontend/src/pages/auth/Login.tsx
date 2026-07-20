import { type FormEvent, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import AuthLayout from "./AuthLayout";
import GoogleButton from "./GoogleButton";

interface LocationState {
  from?: { pathname: string };
}

export default function Login() {
  const { login, sessionExpired, clearSessionExpired } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login({ email, password });
      clearSessionExpired();
      const state = location.state as LocationState | null;
      const redirectTo = state?.from?.pathname ?? "/";
      navigate(redirectTo, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Giriş yapılamadı. Lütfen tekrar deneyin.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Giriş yap"
      footer={
        <>
          <p>
            Hesabınız yok mu? <Link to="/kayit">Ücretsiz hesap oluştur</Link>
          </p>
          <p>
            <Link to="/sifremi-unuttum">Şifrenizi mi unuttunuz?</Link>
          </p>
        </>
      }
    >
      {sessionExpired && (
        <p className="auth-notice" role="alert">
          Oturumunuzun süresi doldu. Devam etmek için lütfen tekrar giriş yapın.
        </p>
      )}
      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      <form className="auth-form" onSubmit={handleSubmit}>
        <label htmlFor="login-email">E-posta</label>
        <input
          id="login-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />

        <label htmlFor="login-password">Parola</label>
        <input
          id="login-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />

        <button type="submit" className="auth-submit" disabled={isSubmitting}>
          {isSubmitting ? "Giriş yapılıyor…" : "Giriş yap"}
        </button>
      </form>

      <div className="auth-divider" role="separator">
        veya
      </div>
      <GoogleButton />
    </AuthLayout>
  );
}
