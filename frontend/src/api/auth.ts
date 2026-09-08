import { apiRequest, googleLoginUrl } from "@/api/client"
import type { AuthSession, Features, Organization } from "@/types"

export const authApi = {
  register(input: { name: string; email: string; password: string; organization_name?: string }) {
    return apiRequest<AuthSession>("/v1/auth/register", { method: "POST", body: input })
  },
  login(input: { email: string; password: string }) {
    return apiRequest<AuthSession>("/v1/auth/login", { method: "POST", body: input })
  },
  logout() {
    return apiRequest<{ ok: boolean }>("/v1/auth/logout", { method: "POST" })
  },
  me() {
    return apiRequest<AuthSession>("/v1/auth/me")
  },
  forgotPassword(email: string) {
    return apiRequest<{ ok: boolean }>("/v1/auth/forgot-password", { method: "POST", body: { email } })
  },
  resetPassword(token: string, password: string) {
    return apiRequest<{ ok: boolean }>("/v1/auth/reset-password", { method: "POST", body: { token, password } })
  },
  verifyEmail(token: string) {
    return apiRequest<{ ok: boolean }>("/v1/auth/verify-email", { method: "POST", body: { token } })
  },
  acceptInvite(input: { token: string; name?: string; password?: string }) {
    return apiRequest<AuthSession>("/v1/users/invite/accept", { method: "POST", body: input })
  },
  startGoogleLogin() {
    window.location.href = googleLoginUrl()
  },
}

export const orgApi = {
  create(name: string) {
    return apiRequest<Organization>("/v1/organizations", { method: "POST", body: { name } })
  },
  me() {
    return apiRequest<Organization>("/v1/organizations/me")
  },
}

export const featuresApi = {
  get() {
    return apiRequest<Features>("/v1/features")
  },
}
