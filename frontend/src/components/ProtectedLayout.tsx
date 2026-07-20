import { Outlet } from "react-router-dom";
import RequireAuth from "../auth/RequireAuth";
import { ThemeProvider } from "../theme/ThemeContext";
import AppShell from "./AppShell";

export default function ProtectedLayout() {
  return (
    <RequireAuth>
      <ThemeProvider>
        <AppShell>
          <Outlet />
        </AppShell>
      </ThemeProvider>
    </RequireAuth>
  );
}
