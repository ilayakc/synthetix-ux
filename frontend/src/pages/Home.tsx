import { useAuth } from "../auth/AuthContext";
import AppShell from "../components/AppShell";
import { ThemeProvider } from "../theme/ThemeContext";
import Dashboard from "./Dashboard";
import Landing from "./Landing";

export default function Home() {
  const { status } = useAuth();

  if (status === "loading") {
    return (
      <div className="auth-loading" role="status">
        Oturum kontrol ediliyor…
      </div>
    );
  }

  if (status === "unauthenticated") return <Landing />;

  return (
    <ThemeProvider>
      <AppShell>
        <Dashboard />
      </AppShell>
    </ThemeProvider>
  );
}
