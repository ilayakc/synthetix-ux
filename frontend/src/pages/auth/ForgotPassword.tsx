import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, requestPasswordReset } from "../../api/client";
import AuthLayout from "./AuthLayout";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [devResetToken, setDevResetToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const response = await requestPasswordReset(email);
      setMessage(response.message);
      setDevResetToken(response.dev_reset_token);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Talep gönderilemedi. Lütfen tekrar deneyin.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Şifrenizi mi unuttunuz?"
      subtitle="E-posta adresinizi girin; sıfırlama talimatlarını gönderelim."
      footer={
        <p>
          <Link to="/giris">Girişe dön</Link>
        </p>
      }
    >
      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      {message ? (
        <div>
          <p className="auth-notice" role="status">
            {message}
          </p>
          {devResetToken && (
            <div className="auth-dev-hint">
              <p>
                <strong>Yalnızca yerel geliştirme:</strong> bu ortamda henüz gerçek bir e-posta
                sağlayıcısı bağlı olmadığı için sıfırlama tokenı doğrudan burada gösterilir.
              </p>
              <p>
                <Link to={`/sifre-sifirla?token=${encodeURIComponent(devResetToken)}`}>
                  Şifreyi sıfırlamaya devam et
                </Link>
              </p>
            </div>
          )}
        </div>
      ) : (
        <form className="auth-form" onSubmit={handleSubmit}>
          <label htmlFor="forgot-email">E-posta</label>
          <input
            id="forgot-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />

          <button type="submit" className="auth-submit" disabled={isSubmitting}>
            {isSubmitting ? "Gönderiliyor…" : "Sıfırlama bağlantısı gönder"}
          </button>
        </form>
      )}
    </AuthLayout>
  );
}
