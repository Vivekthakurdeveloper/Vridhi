import { Navigate, Outlet, Route, Routes } from "react-router-dom"
import { AuthProvider, useAuth } from "@/auth/AuthContext"
import { AppShell } from "@/components/AppShell"
import { LoadingState } from "@/components/States"
import {
  AcceptInvitePage,
  CheckEmailPage,
  ForgotPasswordPage,
  LoginPage,
  OnboardingPage,
  ResetPasswordPage,
  SignupPage,
  VerifyEmailPage,
} from "@/pages/AuthPages"
import { AskPage, SearchPage } from "@/pages/AskSearchPages"
import { DashboardPage } from "@/pages/DashboardPage"
import {
  AuditPage,
  ConnectionsPage,
  KnowledgePage,
  SettingsPage,
  TeamPage,
  UsagePage,
} from "@/pages/WorkspacePages"

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <main className="auth-page"><LoadingState label="Loading..." /></main>
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

function RequireOrg() {
  const { membership, loading } = useAuth()
  if (loading) return <main className="auth-page"><LoadingState label="Loading..." /></main>
  if (!membership) return <Navigate to="/onboarding" replace />
  return <Outlet />
}

function LandingRedirect() {
  const { user, membership, loading } = useAuth()
  if (loading) return <main className="auth-page"><LoadingState label="Loading..." /></main>
  if (!user) return <Navigate to="/login" replace />
  if (!membership) return <Navigate to="/onboarding" replace />
  return <Navigate to="/app/dashboard" replace />
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<LandingRedirect />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/check-email" element={<CheckEmailPage />} />
        <Route path="/invite/accept" element={<AcceptInvitePage />} />
        <Route
          path="/onboarding"
          element={(
            <RequireAuth>
              <OnboardingPage />
            </RequireAuth>
          )}
        />
        <Route
          path="/app"
          element={(
            <RequireAuth>
              <RequireOrg />
            </RequireAuth>
          )}
        >
          <Route element={<AppShell />}>
            <Route path="dashboard" element={<DashboardPage />} />
            <Route path="ask" element={<AskPage />} />
            <Route path="search" element={<SearchPage />} />
            <Route path="knowledge" element={<KnowledgePage />} />
            <Route path="connections" element={<ConnectionsPage />} />
            <Route path="team" element={<TeamPage />} />
            <Route path="usage" element={<UsagePage />} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  )
}
