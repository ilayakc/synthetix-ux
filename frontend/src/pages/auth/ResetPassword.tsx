import { type FormEvent, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, confirmPasswordReset } from "../../api/client";
import AuthLayout from "./AuthLayout";

const MIN_PASSWORD_LENGTH = 8;

export default function ResetPassword() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDone, setIsDone] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setError(`Parola en az ${MIN_PASSWORD_LENGTH} karakter olmalıdır.`);
      return;
    }
    if (newPassword !== confirmation) {
      setError("Parolalar eşleşmiyor.");
      return;
    }

    setIsSubmitting(true);
    try {
      await confirmPasswordReset(token, newPassword);
      setIsDone(true);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Parola sıfırlanamadı. Lütfen tekrar deneyin.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  if (!token) {
    return (
      <AuthLayout
        title="Şifre sıfırlama"
        footer={<Link to="/sifremi-unuttum">Yeni bir bağlantı iste</Link>}
      >
        <p className="auth-error" role="alert">
          Geçersiz veya eksik sıfırlama bağlantısı.
        </p>
      </AuthLayout>
    );
  }

  if (isDone) {
    return (
      <AuthLayout title="Şifre sıfırlama" footer={<Link to="/giris">Girişe dön</Link>}>
        <p className="auth-notice" role="status">
          Parolanız başarıyla güncellendi. Artık yeni parolanızla giriş yapabilirsiniz.
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Yeni parola belirleyin">
      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      <form className="auth-form" onSubmit={handleSubmit}>
        <label htmlFor="reset-password">Yeni parola</label>
        <input
          id="reset-password"
          type="password"
          autoComplete="new-password"
          minLength={MIN_PASSWORD_LENGTH}
          required
          aria-describedby="reset-password-hint"
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
        />
        <p id="reset-password-hint" className="auth-field-hint">
          En az {MIN_PASSWORD_LENGTH} karakter olmalıdır.
        </p>

        <label htmlFor="reset-password-confirm">Yeni parola (tekrar)</label>
        <input
          id="reset-password-confirm"
          type="password"
          autoComplete="new-password"
          minLength={MIN_PASSWORD_LENGTH}
          required
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
        />

        <button type="submit" className="auth-submit" disabled={isSubmitting}>
          {isSubmitting ? "Kaydediliyor…" : "Parolayı güncelle"}
        </button>
      </form>
    </AuthLayout>
  );
}
