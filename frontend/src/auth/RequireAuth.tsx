import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext";

export default function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return (
      <div className="auth-loading" role="status">
        Oturum kontrol ediliyor…
      </div>
    );
  }

  if (status === "unauthenticated") {
    return <Navigate to="/giris" replace state={{ from: location }} />;
  }

  return <>{children}</>;
}
