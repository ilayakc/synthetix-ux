import { useAuth } from "../auth/AuthContext";
import { Navigate } from "react-router-dom";
import AppShell from "../components/AppShell";
import { ThemeProvider } from "../theme/ThemeContext";
import Dashboard from "./Dashboard";
import Landing from "./Landing";

export default function Home() {
  const { status, session } = useAuth();

  if (status === "loading") {
    return (
      <div className="auth-loading" role="status">
        Oturum kontrol ediliyor…
      </div>
    );
  }

  if (status === "unauthenticated") return <Landing />;
  if (session?.is_platform_admin) return <Navigate to="/yonetim" replace />;

  return (
    <ThemeProvider>
      <AppShell>
        <Dashboard />
      </AppShell>
    </ThemeProvider>
  );
}
