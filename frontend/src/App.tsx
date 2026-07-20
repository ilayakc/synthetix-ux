import { Navigate, Route, Routes } from "react-router-dom";
import ProtectedLayout from "./components/ProtectedLayout";
import GuestOnly from "./auth/GuestOnly";
import RouteFallback from "./auth/RouteFallback";
import ChipTopUp from "./pages/ChipTopUp";
import Dashboard from "./pages/Dashboard";
import KullanimVeChip from "./pages/KullanimVeChip";
import ModuleCatalog from "./pages/ModuleCatalog";
import Placeholder from "./pages/Placeholder";
import PersonaPresets from "./pages/PersonaPresets";
import ProjectDetail from "./pages/ProjectDetail";
import Projects from "./pages/Projects";
import ReportDetail from "./pages/ReportDetail";
import Reports from "./pages/Reports";
import Settings from "./pages/settings/Settings";
import Simulations from "./pages/Simulations";
import TestWizard from "./pages/wizard/TestWizard";
import ForgotPassword from "./pages/auth/ForgotPassword";
import Login from "./pages/auth/Login";
import Register from "./pages/auth/Register";
import ResetPassword from "./pages/auth/ResetPassword";

export default function App() {
  return (
    <Routes>
      <Route
        path="/giris"
        element={
          <GuestOnly>
            <Login />
          </GuestOnly>
        }
      />
      <Route
        path="/kayit"
        element={
          <GuestOnly>
            <Register />
          </GuestOnly>
        }
      />
      <Route path="/register" element={<Navigate to="/kayit" replace />} />
      <Route path="/sifremi-unuttum" element={<ForgotPassword />} />
      <Route path="/sifre-sifirla" element={<ResetPassword />} />

      <Route element={<ProtectedLayout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/projeler" element={<Projects />} />
        <Route path="/projeler/:projectId" element={<ProjectDetail />} />
        <Route path="/tests/new" element={<TestWizard />} />
        <Route path="/analiz-modulleri" element={<ModuleCatalog />} />
        <Route path="/chip-yukle" element={<ChipTopUp />} />
        <Route path="/personalar" element={<PersonaPresets />} />
        <Route path="/simulasyonlar" element={<Simulations />} />
        <Route path="/raporlar" element={<Reports />} />
        <Route path="/raporlar/:reportId" element={<ReportDetail />} />
        <Route path="/kullanim-ve-chip" element={<KullanimVeChip />} />
        <Route path="/ayarlar" element={<Settings />} />
        <Route path="/yardim" element={<Placeholder title="Yardım" />} />
      </Route>

      <Route path="*" element={<RouteFallback />} />
    </Routes>
  );
}
