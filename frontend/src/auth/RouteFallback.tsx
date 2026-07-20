import { Navigate } from "react-router-dom";
import { useAuth } from "./AuthContext";

export default function RouteFallback() {
  const { status } = useAuth();

  if (status === "loading") {
    return (
      <div className="auth-loading" role="status">
        Oturum kontrol ediliyor…
      </div>
    );
  }

  return <Navigate to={status === "authenticated" ? "/" : "/giris"} replace />;
}
