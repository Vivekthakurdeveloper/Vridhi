import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"
import { authApi, orgApi } from "@/api/auth"
import { ApiError, userFacingError } from "@/api/client"
import type { AuthSession, Membership, User } from "@/types"

type AuthState = {
  user: User | null
  membership: Membership | null
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  login: (email: string, password: string) => Promise<AuthSession>
  register: (input: { name: string; email: string; password: string; organization_name?: string }) => Promise<AuthSession>
  logout: () => Promise<void>
  createOrganization: (name: string) => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [membership, setMembership] = useState<Membership | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const applySession = useCallback((session: AuthSession | null) => {
    setUser(session?.user ?? null)
    setMembership(session?.membership ?? null)
  }, [])

  const refresh = useCallback(async () => {
    try {
      const session = await authApi.me()
      applySession(session)
      setError(null)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        applySession(null)
        setError(null)
      } else {
        setError(userFacingError(err))
      }
    } finally {
      setLoading(false)
    }
  }, [applySession])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const login = useCallback(async (email: string, password: string) => {
    const session = await authApi.login({ email, password })
    applySession(session)
    return session
  }, [applySession])

  const register = useCallback(async (input: { name: string; email: string; password: string; organization_name?: string }) => {
    const session = await authApi.register(input)
    applySession(session)
    return session
  }, [applySession])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } finally {
      applySession(null)
    }
  }, [applySession])

  const createOrganization = useCallback(async (name: string) => {
    await orgApi.create(name)
    await refresh()
  }, [refresh])

  const value = useMemo(
    () => ({ user, membership, loading, error, refresh, login, register, logout, createOrganization }),
    [user, membership, loading, error, refresh, login, register, logout, createOrganization],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
