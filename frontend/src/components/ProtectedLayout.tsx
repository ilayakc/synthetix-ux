import { Navigate, Outlet, useLocation } from "react-router-dom";
import RequireAuth from "../auth/RequireAuth";
import { useAuth } from "../auth/AuthContext";
import { ThemeProvider } from "../theme/ThemeContext";
import AppShell from "./AppShell";

export default function ProtectedLayout() {
  return (
    <RequireAuth>
      <UserWorkspace />
    </RequireAuth>
  );
}

function UserWorkspace() {
  const { session } = useAuth();
  const location = useLocation();
  if (session?.is_platform_admin) return <Navigate to="/yonetim" replace />;
  if (session?.is_demo && location.pathname === "/tests/new") {
    return <Navigate to="/raporlar" replace />;
  }

  return (
    <ThemeProvider>
      <AppShell>
        {session?.is_demo && (
          <p className="auth-notice" role="status">
            Canlı demo salt okunur. Proje ve raporları inceleyebilir, tüm ekranlarda gezinebilirsiniz;
            kalıcı değişiklikler ve Chip talepleri kapalıdır.
          </p>
        )}
        <Outlet />
      </AppShell>
    </ThemeProvider>
  );
}
