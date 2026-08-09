import { Outlet } from "react-router-dom";
import RequirePlatformAdmin from "../auth/RequirePlatformAdmin";
import { ThemeProvider } from "../theme/ThemeContext";
import AdminShell from "./AdminShell";

export default function AdminProtectedLayout() {
  return (
    <RequirePlatformAdmin>
      <ThemeProvider>
        <AdminShell>
          <Outlet />
        </AdminShell>
      </ThemeProvider>
    </RequirePlatformAdmin>
  );
}
