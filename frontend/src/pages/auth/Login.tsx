import { type FormEvent, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import AuthLayout from "./AuthLayout";

interface LocationState {
  from?: { pathname: string };
}

const DEMO_USER_EMAIL = "synthetix.demo.user@example.com";
const DEMO_ADMIN_EMAIL = "synthetix.demo.admin@example.com";
const DEMO_PASSWORD = "DemoSynthetix123!";
type LoginMode = "user" | "admin";

export default function Login() {
  const { login, logout, sessionExpired, clearSessionExpired } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialMode: LoginMode = searchParams.get("tip") === "yonetici" ? "admin" : "user";

  const [mode, setMode] = useState<LoginMode>(initialMode);
  const [email, setEmail] = useState(
    import.meta.env.DEV ? (initialMode === "admin" ? DEMO_ADMIN_EMAIL : DEMO_USER_EMAIL) : "",
  );
  const [password, setPassword] = useState(import.meta.env.DEV ? DEMO_PASSWORD : "");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const authenticatedSession = await login({ email, password });
      if (mode === "user" && authenticatedSession.is_platform_admin) {
        await logout();
        setError("Bu hesap için Yönetici girişi sekmesini seçin.");
        return;
      }
      if (mode === "admin" && !authenticatedSession.is_platform_admin) {
        await logout();
        setError("Bu hesap platform yöneticisi yetkisine sahip değil.");
        return;
      }

      clearSessionExpired();
      if (mode === "admin") {
        navigate("/yonetim", { replace: true });
        return;
      }

      const state = location.state as LocationState | null;
      const redirectTo = state?.from?.pathname ?? "/";
      navigate(redirectTo, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Giriş yapılamadı. Lütfen tekrar deneyin.");
    } finally {
      setIsSubmitting(false);
    }
  }

  function selectMode(nextMode: LoginMode) {
    setMode(nextMode);
    setError(null);
    setEmail(
      import.meta.env.DEV ? (nextMode === "admin" ? DEMO_ADMIN_EMAIL : DEMO_USER_EMAIL) : "",
    );
    setPassword(import.meta.env.DEV ? DEMO_PASSWORD : "");
    setSearchParams(nextMode === "admin" ? { tip: "yonetici" } : {}, { replace: true });
  }

  return (
    <AuthLayout
      title={mode === "admin" ? "Yönetici girişi" : "Giriş yap"}
      subtitle={
        mode === "admin"
          ? "Platform operasyonları ve Chip talepleri için yönetim alanına erişin."
          : "Hesabınıza ve şirket çalışma alanınıza erişin."
      }
      footer={
        mode === "user" ? (
          <>
            <p>
              Hesabınız yok mu? <Link to="/kayit">Ücretsiz hesap oluştur</Link>
            </p>
            <p>
              <Link to="/sifremi-unuttum">Şifrenizi mi unuttunuz?</Link>
            </p>
          </>
        ) : undefined
      }
    >
      <div className="login-mode-tabs" role="tablist" aria-label="Giriş türü">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "user"}
          className={mode === "user" ? "is-active" : ""}
          onClick={() => selectMode("user")}
        >
          Kullanıcı girişi
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "admin"}
          className={mode === "admin" ? "is-active" : ""}
          onClick={() => selectMode("admin")}
        >
          Yönetici girişi
        </button>
      </div>

      {mode === "user" && sessionExpired && (
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
        <label htmlFor="login-email">{mode === "admin" ? "Yönetici e-postası" : "E-posta"}</label>
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
          {isSubmitting
            ? "Giriş yapılıyor…"
            : mode === "admin"
              ? "Yönetim alanına gir"
              : "Giriş yap"}
        </button>
      </form>
    </AuthLayout>
  );
}
