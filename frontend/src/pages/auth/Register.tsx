import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import AuthLayout from "./AuthLayout";
import GoogleButton from "./GoogleButton";

const MIN_PASSWORD_LENGTH = 8;

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();

  const [organizationName, setOrganizationName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Parola en az ${MIN_PASSWORD_LENGTH} karakter olmalıdır.`);
      return;
    }

    setIsSubmitting(true);
    try {
      await register({
        email,
        password,
        organization_name: organizationName,
        display_name: displayName || undefined,
      });
      navigate("/", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Kayıt oluşturulamadı. Lütfen tekrar deneyin.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Hesap oluştur"
      subtitle="0 Chip bakiyesiyle başlayın; 2 ücretsiz kullanım hakkınız otomatik tanımlanır."
      footer={
        <p>
          Zaten hesabınız var mı? <Link to="/giris">Giriş yapın</Link>
        </p>
      }
    >
      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      <form className="auth-form" onSubmit={handleSubmit}>
        <label htmlFor="register-org">Şirket / organizasyon adı</label>
        <input
          id="register-org"
          type="text"
          required
          value={organizationName}
          onChange={(event) => setOrganizationName(event.target.value)}
        />

        <label htmlFor="register-name">Adınız (opsiyonel)</label>
        <input
          id="register-name"
          type="text"
          autoComplete="name"
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
        />

        <label htmlFor="register-email">E-posta</label>
        <input
          id="register-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />

        <label htmlFor="register-password">Parola</label>
        <input
          id="register-password"
          type="password"
          autoComplete="new-password"
          minLength={MIN_PASSWORD_LENGTH}
          required
          aria-describedby="register-password-hint"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <p id="register-password-hint" className="auth-field-hint">
          En az {MIN_PASSWORD_LENGTH} karakter olmalıdır.
        </p>

        <button type="submit" className="auth-submit" disabled={isSubmitting}>
          {isSubmitting ? "Hesap oluşturuluyor…" : "Hesap oluştur"}
        </button>
      </form>

      <div className="auth-divider" role="separator">
        veya
      </div>
      <GoogleButton />
    </AuthLayout>
  );
}
