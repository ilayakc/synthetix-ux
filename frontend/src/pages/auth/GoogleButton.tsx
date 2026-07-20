export default function GoogleButton() {
  return (
    <button
      type="button"
      className="auth-google-button"
      disabled
      title="Google ile giriş yakında kullanıma açılacak"
    >
      <span className="auth-google-button__icon" aria-hidden="true">
        G
      </span>
      Google ile devam et (Yakında)
    </button>
  );
}
