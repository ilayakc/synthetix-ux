import { Navigate, Outlet } from "react-router-dom";
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
  if (session?.is_platform_admin) return <Navigate to="/yonetim" replace />;

  return (
    <ThemeProvider>
      <AppShell>
        <Outlet />
      </AppShell>
    </ThemeProvider>
  );
}
