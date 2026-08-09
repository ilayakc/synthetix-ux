import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "./AuthContext";

export default function RequirePlatformAdmin({ children }: { children: ReactNode }) {
  const { status, session } = useAuth();

  if (status === "loading") {
    return <p className="page-placeholder">Yetki kontrol ediliyor…</p>;
  }
  if (status !== "authenticated") {
    return <Navigate to="/giris" replace />;
  }
  if (!session?.is_platform_admin) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}
