import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { useAuth } from "@/auth/AuthContext"
import { userFacingError } from "@/api/client"
import { dashboardApi } from "@/api/workspace"
import { EmptyState, ErrorState, LoadingState } from "@/components/States"
import { Icon } from "@/components/Icon"
import type { Dashboard } from "@/types"
import { formatAction, formatDateTime } from "@/utils/format"

export function DashboardPage() {
  const { user, membership } = useAuth()
  const [data, setData] = useState<Dashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      setData(await dashboardApi.get())
    } catch (err) {
      setError(userFacingError(err))
      setData(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const firstName = user?.name?.split(/\s+/)[0] || "there"
  const isEmptyWorkspace = data && data.documents === 0 && data.connections_connected === 0

  return (
    <main className="page dashboard">
      <div className="page-head">
        <div>
          <span className="eyebrow">WORKSPACE</span>
          <h1>Good day, {firstName}</h1>
          <p>{membership ? `${membership.organization_name} knowledge workspace.` : "Organization information unavailable."}</p>
        </div>
        <Link className="primary-button" to="/app/ask">Ask Vridhi <Icon name="arrow" size={16} /></Link>
      </div>

      {loading ? <LoadingState label="Loading dashboard..." /> : null}
      {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}

      {!loading && !error && data && isEmptyWorkspace ? (
        <EmptyState
          icon="link"
          title="Welcome to Vridhi"
          description="Your workspace isn't connected yet. Connect a source or upload documents to start asking questions."
          action={
            <div className="button-row">
              <Link className="primary-button" to="/app/connections">Connect a source</Link>
              <Link className="secondary-button" to="/app/knowledge">View knowledge</Link>
            </div>
          }
        />
      ) : null}

      {!loading && !error && data && !isEmptyWorkspace ? (
        <>
          <div className="stats">
            <article>
              <span className="eyebrow">KNOWLEDGE</span>
              <strong>{data.documents.toLocaleString()}</strong>
              <p>Documents indexed</p>
              <small>{data.last_sync_at ? `Last sync ${formatDateTime(data.last_sync_at)}` : "No sync completed yet"}</small>
            </article>
            <article>
              <span className="eyebrow">USAGE</span>
              <strong>{data.questions.toLocaleString()}</strong>
              <p>Questions asked</p>
              <small><b>{data.users}</b> active members</small>
            </article>
            <article>
              <span className="eyebrow">CONNECTIONS</span>
              <p className="metric-line">{data.connections_connected} connected · {data.connections_available} available</p>
              <Link className="small-link" to="/app/connections">Manage connections <Icon name="arrow" size={13} /></Link>
            </article>
          </div>
          <section className="activity">
            <div className="section-title">
              <div>
                <span className="eyebrow">WORKSPACE</span>
                <h2>Recent activity</h2>
              </div>
              <Link className="small-link" to="/app/audit">View audit log <Icon name="arrow" size={13} /></Link>
            </div>
            {data.recent_activity.length === 0 ? (
              <p className="muted">No activity recorded yet.</p>
            ) : (
              data.recent_activity.map((item) => (
                <div className="activity-row" key={item.id}>
                  <span className="person-avatar">·</span>
                  <p>
                    <b>{formatAction(item.action)}</b>
                    <br />
                    <span>{formatDateTime(item.created_at)}</span>
                  </p>
                </div>
              ))
            )}
          </section>
        </>
      ) : null}
    </main>
  )
}
