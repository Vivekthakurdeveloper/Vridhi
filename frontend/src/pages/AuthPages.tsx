import { FormEvent, useEffect, useState } from "react"
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom"
import { useAuth } from "@/auth/AuthContext"
import { authApi, featuresApi } from "@/api/auth"
import { userFacingError } from "@/api/client"
import { LoadingState } from "@/components/States"

function GoogleButton({ enabled, label }: { enabled: boolean; label: string }) {
  if (!enabled) return null
  return (
    <>
      <div className="auth-divider"><span>or</span></div>
      <button type="button" className="secondary-button full" onClick={() => authApi.startGoogleLogin()}>
        {label}
      </button>
    </>
  )
}

function useGoogleLoginEnabled() {
  const [enabled, setEnabled] = useState(false)
  useEffect(() => {
    void featuresApi.get()
      .then((f) => setEnabled(Boolean(f.google_login_enabled)))
      .catch(() => setEnabled(false))
  }, [])
  return enabled
}

export function LoginPage() {
  const { user, membership, loading, login } = useAuth()
  const navigate = useNavigate()
  const googleEnabled = useGoogleLoginEnabled()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!loading && user) {
    return <Navigate to={membership ? "/app/dashboard" : "/onboarding"} replace />
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const session = await login(email.trim(), password)
      navigate(session.membership ? "/app/dashboard" : "/onboarding")
    } catch (err) {
      setError(userFacingError(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="brand auth-brand"><span className="brand-mark"><i /><i /><i /></span><span>vridhi<span className="brand-dot">.ai</span></span></div>
        <h1>Sign in</h1>
        <p>Access your organization’s knowledge workspace.</p>
        {loading ? <LoadingState label="Checking session..." /> : null}
        <form onSubmit={onSubmit} className="auth-form">
          <label>
            Email
            <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            Password
            <input type="password" autoComplete="current-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          <button className="primary-button full" type="submit" disabled={submitting}>{submitting ? "Signing in..." : "Sign in"}</button>
        </form>
        <GoogleButton enabled={googleEnabled} label="Continue with Google" />
        <p className="auth-foot"><Link to="/forgot-password">Forgot password?</Link> · <Link to="/signup">Create account</Link></p>
      </div>
    </main>
  )
}

export function SignupPage() {
  const { user, membership, loading, register } = useAuth()
  const navigate = useNavigate()
  const googleEnabled = useGoogleLoginEnabled()
  const [name, setName] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [organizationName, setOrganizationName] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!loading && user) {
    return <Navigate to={membership ? "/app/dashboard" : "/onboarding"} replace />
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const session = await register({
        name: name.trim(),
        email: email.trim(),
        password,
        organization_name: organizationName.trim() || undefined,
      })
      navigate(session.membership ? "/app/dashboard" : "/onboarding")
    } catch (err) {
      setError(userFacingError(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="brand auth-brand"><span className="brand-mark"><i /><i /><i /></span><span>vridhi<span className="brand-dot">.ai</span></span></div>
        <h1>Create your account</h1>
        <p>Start a workspace for your company knowledge.</p>
        <form onSubmit={onSubmit} className="auth-form">
          <label>
            Full name
            <input required value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
          </label>
          <label>
            Work email
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </label>
          <label>
            Organization name
            <input value={organizationName} onChange={(e) => setOrganizationName(e.target.value)} placeholder="Optional — you can create this next" />
          </label>
          <label>
            Password
            <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          </label>
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          <button className="primary-button full" type="submit" disabled={submitting}>{submitting ? "Creating..." : "Create account"}</button>
        </form>
        <GoogleButton enabled={googleEnabled} label="Sign up with Google" />
        <p className="auth-foot">Already have an account? <Link to="/login">Sign in</Link></p>
      </div>
    </main>
  )
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("")
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await authApi.forgotPassword(email.trim())
      setDone(true)
    } catch (err) {
      setError(userFacingError(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <h1>Reset password</h1>
        <p>We’ll email a reset link if an account exists for that address.</p>
        {done ? (
          <p className="form-success" role="status">If an account exists, a reset email has been sent. Check your inbox (or API logs in local development).</p>
        ) : (
          <form onSubmit={onSubmit} className="auth-form">
            <label>
              Email
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            {error ? <p className="form-error" role="alert">{error}</p> : null}
            <button className="primary-button full" type="submit" disabled={submitting}>{submitting ? "Sending..." : "Send reset link"}</button>
          </form>
        )}
        <p className="auth-foot"><Link to="/login">Back to sign in</Link></p>
      </div>
    </main>
  )
}

export function ResetPasswordPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const token = params.get("token") || ""
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (password !== confirm) {
      setError("Passwords do not match.")
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await authApi.resetPassword(token, password)
      setDone(true)
      setTimeout(() => navigate("/login"), 1500)
    } catch (err) {
      setError(userFacingError(err))
    } finally {
      setSubmitting(false)
    }
  }

  if (!token) {
    return (
      <main className="auth-page">
        <div className="auth-card">
          <h1>Invalid reset link</h1>
          <p>This password reset link is missing a token.</p>
          <p className="auth-foot"><Link to="/forgot-password">Request a new link</Link></p>
        </div>
      </main>
    )
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <h1>Choose a new password</h1>
        <p>Enter a new password for your Vridhi account.</p>
        {done ? (
          <p className="form-success" role="status">Password updated. Redirecting to sign in…</p>
        ) : (
          <form onSubmit={onSubmit} className="auth-form">
            <label>
              New password
              <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
            </label>
            <label>
              Confirm password
              <input type="password" required minLength={8} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
            </label>
            {error ? <p className="form-error" role="alert">{error}</p> : null}
            <button className="primary-button full" type="submit" disabled={submitting}>{submitting ? "Updating..." : "Update password"}</button>
          </form>
        )}
      </div>
    </main>
  )
}

export function VerifyEmailPage() {
  const [params] = useSearchParams()
  const token = params.get("token") || ""
  const [status, setStatus] = useState<"idle" | "loading" | "ok" | "error">(token ? "loading" : "error")
  const [message, setMessage] = useState(token ? "Verifying your email…" : "This verification link is missing a token.")

  useEffect(() => {
    if (!token) return
    void (async () => {
      try {
        await authApi.verifyEmail(token)
        setStatus("ok")
        setMessage("Your email has been verified.")
      } catch (err) {
        setStatus("error")
        setMessage(userFacingError(err))
      }
    })()
  }, [token])

  return (
    <main className="auth-page">
      <div className="auth-card">
        <h1>Email verification</h1>
        {status === "loading" ? <LoadingState label={message} /> : null}
        {status === "ok" ? <p className="form-success" role="status">{message}</p> : null}
        {status === "error" ? <p className="form-error" role="alert">{message}</p> : null}
        <p className="auth-foot"><Link to="/login">Continue to sign in</Link></p>
      </div>
    </main>
  )
}

export function AcceptInvitePage() {
  const { user, refresh } = useAuth()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const token = params.get("token") || ""
  const [name, setName] = useState("")
  const [password, setPassword] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!token) {
      setError("This invite link is missing a token.")
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const session = await authApi.acceptInvite({
        token,
        name: user ? undefined : name.trim() || undefined,
        password: user ? undefined : password || undefined,
      })
      await refresh()
      navigate(session.membership ? "/app/dashboard" : "/onboarding")
    } catch (err) {
      setError(userFacingError(err))
    } finally {
      setSubmitting(false)
    }
  }

  if (!token) {
    return (
      <main className="auth-page">
        <div className="auth-card">
          <h1>Invalid invite</h1>
          <p>This invite link is missing a token.</p>
          <p className="auth-foot"><Link to="/login">Sign in</Link></p>
        </div>
      </main>
    )
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <h1>Accept invitation</h1>
        <p>{user ? `Signed in as ${user.email}. Join the organization with this invite.` : "Create your account to join the organization."}</p>
        <form onSubmit={onSubmit} className="auth-form">
          {!user ? (
            <>
              <label>
                Full name
                <input required value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label>
                Password
                <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
              </label>
            </>
          ) : null}
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          <button className="primary-button full" type="submit" disabled={submitting}>
            {submitting ? "Joining..." : "Join organization"}
          </button>
        </form>
        {!user ? <p className="auth-foot">Already have an account? <Link to="/login">Sign in</Link> first, then reopen this invite link.</p> : null}
      </div>
    </main>
  )
}

export function OnboardingPage() {
  const { user, membership, createOrganization } = useAuth()
  const navigate = useNavigate()
  const [name, setName] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!user) return <Navigate to="/login" replace />
  if (membership) return <Navigate to="/app/dashboard" replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await createOrganization(name.trim())
      navigate("/app/dashboard")
    } catch (err) {
      setError(userFacingError(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <span className="eyebrow">WELCOME</span>
        <h1>Create your organization</h1>
        <p>Hi {user.name}. Set up a workspace before connecting knowledge sources.</p>
        <form onSubmit={onSubmit} className="auth-form">
          <label>
            Organization name
            <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Your company name" />
          </label>
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          <button className="primary-button full" type="submit" disabled={submitting}>{submitting ? "Creating..." : "Continue"}</button>
        </form>
      </div>
    </main>
  )
}
