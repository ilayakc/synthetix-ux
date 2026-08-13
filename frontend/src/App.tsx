import { Navigate, Route, Routes } from "react-router-dom";
import { useRouteChangeLogging } from "./lib/useRouteChangeLogging";
import { useAnalyticsPageViews } from "./lib/analytics";
import ConsentBanner from "./components/ConsentBanner";
import ProtectedLayout from "./components/ProtectedLayout";
import GuestOnly from "./auth/GuestOnly";
import RouteFallback from "./auth/RouteFallback";
import KullanimVeChip from "./pages/KullanimVeChip";
import ModuleCatalog from "./pages/ModuleCatalog";
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
import Home from "./pages/Home";
import AdminDashboard from "./pages/AdminDashboard";
import AdminSettings from "./pages/AdminSettings";
import AdminTraffic from "./pages/AdminTraffic";
import AdminProtectedLayout from "./components/AdminProtectedLayout";
import Help from "./pages/Help";
import NasilCalisiyor from "./pages/NasilCalisiyor";

export default function App() {
  useRouteChangeLogging();
  useAnalyticsPageViews();

  return (
    <>
      <ConsentBanner />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/nasil-calisiyor" element={<NasilCalisiyor />} />
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
        <Route path="/yonetici-girisi" element={<Navigate to="/giris?tip=yonetici" replace />} />

        <Route element={<AdminProtectedLayout />}>
          <Route path="/yonetim" element={<AdminDashboard section="overview" />} />
          <Route path="/yonetim/trafik" element={<AdminTraffic />} />
          <Route path="/yonetim/chip-talepleri" element={<AdminDashboard section="topups" />} />
          <Route path="/yonetim/ai-islemleri" element={<AdminDashboard section="ai" />} />
          <Route path="/yonetim/giris-kayitlari" element={<AdminDashboard section="logins" />} />
          <Route path="/yonetim/islem-kayitlari" element={<AdminDashboard section="audit" />} />
          <Route path="/yonetim/ayarlar" element={<AdminSettings />} />
        </Route>

        <Route element={<ProtectedLayout />}>
          <Route path="/projeler" element={<Projects />} />
          <Route path="/projeler/:projectId" element={<ProjectDetail />} />
          <Route path="/tests/new" element={<TestWizard />} />
          <Route path="/analiz-modulleri" element={<ModuleCatalog />} />
          <Route path="/chip-yukle" element={<Navigate to="/kullanim-ve-chip" replace />} />
          <Route path="/personalar" element={<PersonaPresets />} />
          <Route path="/simulasyonlar" element={<Simulations />} />
          <Route path="/raporlar" element={<Reports />} />
          <Route path="/raporlar/:reportId" element={<ReportDetail />} />
          <Route path="/kullanim-ve-chip" element={<KullanimVeChip />} />
          <Route path="/ayarlar" element={<Settings />} />
          <Route path="/yardim" element={<Help />} />
        </Route>

        <Route path="*" element={<RouteFallback />} />
      </Routes>
    </>
  );
}
