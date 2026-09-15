import { FormEvent, useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { useAuth } from "@/auth/AuthContext"
import { ApiError, userFacingError } from "@/api/client"
import { featuresApi } from "@/api/auth"
import {
  auditApi,
  connectorsApi,
  documentsApi,
  driveApi,
  jobsApi,
  teamApi,
  usageApi,
  workspaceEnterpriseApi,
} from "@/api/workspace"
import { EmptyState, ErrorState, LoadingState, UnavailableState } from "@/components/States"
import { Icon } from "@/components/Icon"
import type {
  Connector,
  DocumentItem,
  DocumentPreview,
  DriveConnection,
  DriveFolder,
  Features,
  Invite,
  Member,
  MemberRole,
  PaginatedAudit,
  PaginatedDocuments,
  SyncJob,
  Usage,
  WorkspaceEnterpriseStatus,
} from "@/types"
import { formatAction, formatDateTime, initials } from "@/utils/format"

function statusLabel(status: Connector["status"]): string {
  switch (status) {
    case "not_implemented":
      return "Coming soon"
    case "not_configured":
      return "Not configured"
    case "connected":
      return "Connected"
    case "disconnected":
      return "Disconnected"
    case "connecting":
      return "Connecting"
    case "syncing":
      return "Syncing"
    case "sync_failed":
      return "Sync failed"
    case "auth_required":
      return "Auth required"
    case "available":
      return "Available"
    default:
      return status
  }
}

export function KnowledgePage() {
  const [data, setData] = useState<PaginatedDocuments | null>(null)
  const [members, setMembers] = useState<Member[]>([])
  const [features, setFeatures] = useState<Features | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [visibility, setVisibility] = useState<"private" | "org" | "selected">("private")
  const [selectedUsers, setSelectedUsers] = useState<string[]>([])
  const [preview, setPreview] = useState<DocumentPreview | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const [docs, feats] = await Promise.all([
        documentsApi.list(),
        featuresApi.get(),
      ])
      setData(docs)
      setFeatures(feats)
      try {
        setMembers(await teamApi.list())
      } catch {
        setMembers([])
      }
    } catch (err) {
      setError(userFacingError(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  useEffect(() => {
    if (!data?.items.some((d) => d.status === "pending" || d.status === "processing")) return
    const timer = window.setInterval(() => {
      void documentsApi.list().then(setData).catch(() => undefined)
    }, 2500)
    return () => window.clearInterval(timer)
  }, [data])

  async function handleFiles(files: FileList | File[]) {
    const list = Array.from(files)
    if (!list.length) return
    if (!features?.file_upload_enabled) {
      setUploadError("File upload isn't available yet.")
      return
    }
    setUploading(true)
    setUploadError(null)
    try {
      for (const file of list) {
        await documentsApi.upload(file, visibility, visibility === "selected" ? selectedUsers : [])
      }
      await load()
    } catch (err) {
      setUploadError(userFacingError(err))
    } finally {
      setUploading(false)
    }
  }

  async function onDelete(id: string) {
    try {
      await documentsApi.delete(id)
      await load()
    } catch (err) {
      setUploadError(userFacingError(err))
    }
  }

  async function onPreview(id: string) {
    try {
      setPreview(await documentsApi.preview(id))
    } catch (err) {
      setUploadError(userFacingError(err))
    }
  }

  function toggleUser(id: string) {
    setSelectedUsers((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  const uploadEnabled = Boolean(features?.file_upload_enabled)
  const extensions = features?.upload_allowed_extensions?.join(", ").toUpperCase() || "PDF, DOCX, XLSX, PPTX, TXT, CSV"

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <span className="eyebrow">KNOWLEDGE</span>
          <h1>Knowledge</h1>
          <p>Upload documents and track indexing status for your workspace.</p>
        </div>
      </div>

      {uploadEnabled ? (
        <section className="upload-panel">
          <div
            className={`dropzone ${dragOver ? "dropzone-active" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragOver(false)
              void handleFiles(e.dataTransfer.files)
            }}
          >
            <Icon name="upload" />
            <strong>{uploading ? "Uploading…" : "Drag and drop files here"}</strong>
            <span>or choose files · {extensions}</span>
            <label className="primary-button">
              Browse files
              <input
                type="file"
                multiple
                hidden
                disabled={uploading}
                accept={features?.upload_allowed_extensions?.map((e) => `.${e}`).join(",") || undefined}
                onChange={(e) => {
                  if (e.target.files) void handleFiles(e.target.files)
                  e.target.value = ""
                }}
              />
            </label>
          </div>
          <div className="upload-options">
            <label>
              Visibility
              <select value={visibility} onChange={(e) => setVisibility(e.target.value as typeof visibility)}>
                <option value="private">Private (only you + admins)</option>
                <option value="org">Organization</option>
                <option value="selected">Selected users</option>
              </select>
            </label>
            {visibility === "selected" ? (
              <div className="user-pick">
                <span>Share with</span>
                <div className="user-pick-list">
                  {members.map((m) => (
                    <label key={m.user_id} className="user-pick-item">
                      <input
                        type="checkbox"
                        checked={selectedUsers.includes(m.user_id)}
                        onChange={() => toggleUser(m.user_id)}
                      />
                      {m.name}
                    </label>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </section>
      ) : null}

      {uploadError ? <ErrorState message={uploadError} /> : null}
      {loading ? <LoadingState label="Loading documents..." /> : null}
      {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}

      {!loading && !error && data && data.total === 0 ? (
        <EmptyState
          icon="book"
          title={uploadEnabled ? "No documents yet" : "No knowledge has been indexed yet"}
          description={
            uploadEnabled
              ? "Drop a PDF, DOCX, XLSX, PPTX, or TXT file above to start indexing."
              : "Connect a source or enable file upload to start asking questions."
          }
          action={
            uploadEnabled ? undefined : (
              <Link className="primary-button" to="/app/connections">Connect a source</Link>
            )
          }
        />
      ) : null}

      {!loading && !error && data && data.items.length > 0 ? (
        <div className="data-table" role="table" aria-label="Documents">
          {data.items.map((doc) => (
            <div className="data-row" role="row" key={doc.id}>
              <div>
                <strong>{doc.title}</strong>
                <small>
                  {doc.source || "file_upload"} · {doc.visibility} · {doc.status}
                  {doc.job_status ? ` · job ${doc.job_status}` : ""}
                  {doc.error_message ? ` · ${doc.error_message}` : ""}
                </small>
              </div>
              <div className="row-actions">
                <time>{formatDateTime(doc.updated_at)}</time>
                <button type="button" className="text-button" onClick={() => void onPreview(doc.id)}>Preview</button>
                <button type="button" className="text-button" onClick={() => void onDelete(doc.id)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {preview ? (
        <div className="preview-panel">
          <div className="preview-head">
            <h2>{preview.title}</h2>
            <button type="button" className="text-button" onClick={() => setPreview(null)}>Close</button>
          </div>
          <p className="muted">{preview.status} · {preview.chunk_count} chunk(s){preview.truncated ? " · truncated" : ""}</p>
          <pre className="preview-body">{preview.preview || "No preview available yet. Wait until processing finishes."}</pre>
        </div>
      ) : null}
    </main>
  )
}

export function ConnectionsPage() {
  const { membership } = useAuth()
  const [connectors, setConnectors] = useState<Connector[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  const [driveDetail, setDriveDetail] = useState<DriveConnection | null>(null)
  const [folders, setFolders] = useState<DriveFolder[]>([])
  const [selectedFolders, setSelectedFolders] = useState<string[]>([])
  const [history, setHistory] = useState<SyncJob[]>([])
  const [failedDocs, setFailedDocs] = useState<DocumentItem[]>([])
  const [activeJob, setActiveJob] = useState<SyncJob | null>(null)
  const [driveBusy, setDriveBusy] = useState(false)
  const [entDetail, setEntDetail] = useState<WorkspaceEnterpriseStatus | null>(null)
  const [entBusy, setEntBusy] = useState(false)
  const [entDomain, setEntDomain] = useState("")
  const [entKey, setEntKey] = useState("")

  const canManage = membership?.role === "owner" || membership?.role === "admin"
  const drive = connectors.find((c) => c.id === "google_drive")
  const driveConnected =
    !!drive && ["connected", "syncing", "sync_failed"].includes(drive.status)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const res = await connectorsApi.list()
      setConnectors(res.connectors)
      const gd = res.connectors.find((c) => c.id === "google_drive")
      if (gd && ["connected", "syncing", "sync_failed"].includes(gd.status) && canManage) {
        await loadDrivePanel()
      } else {
        setDriveDetail(null)
        setFolders([])
        setHistory([])
        setFailedDocs([])
      }
      if (canManage) {
        try {
          setEntDetail(await workspaceEnterpriseApi.get())
        } catch {
          setEntDetail(null)
        }
      }
    } catch (err) {
      setError(userFacingError(err))
    } finally {
      setLoading(false)
    }
  }

  async function loadDrivePanel() {
    const [detail, folderRes, hist, failed] = await Promise.all([
      driveApi.get(),
      driveApi.folders(),
      driveApi.syncHistory(),
      driveApi.failedDocuments(),
    ])
    setDriveDetail(detail)
    setFolders(folderRes.folders)
    setSelectedFolders(folderRes.selected_folder_ids)
    setHistory(hist.items)
    setFailedDocs(failed.items)
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get("drive") === "connected") {
      setActionMsg("Google Drive connected.")
      window.history.replaceState({}, "", "/app/connections")
    } else if (params.get("drive") === "error") {
      setActionError("Google Drive connection failed. Try again.")
      window.history.replaceState({}, "", "/app/connections")
    }
    void load()
  }, [])

  useEffect(() => {
    if (!activeJob || ["succeeded", "failed", "dead"].includes(activeJob.status)) return
    const timer = window.setInterval(async () => {
      try {
        const job = await jobsApi.get(activeJob.id)
        setActiveJob(job)
        if (["succeeded", "failed", "dead"].includes(job.status)) {
          await load()
        }
      } catch {
        /* ignore poll errors */
      }
    }, 1500)
    return () => window.clearInterval(timer)
  }, [activeJob?.id, activeJob?.status])

  async function onConnect(id: string) {
    setActionError(null)
    setActionMsg(null)
    try {
      if (id === "google_drive") {
        window.location.href = driveApi.oauthStartUrl()
        return
      }
      await connectorsApi.connect(id)
      await load()
    } catch (err) {
      if (err instanceof ApiError && err.status === 501) {
        setActionError(err.message)
        return
      }
      setActionError(userFacingError(err))
    }
  }

  async function onDisconnectDrive() {
    if (!window.confirm("Disconnect Google Drive? Synced documents stay until you delete them.")) return
    setDriveBusy(true)
    setActionError(null)
    try {
      await driveApi.disconnect()
      setActionMsg("Google Drive disconnected.")
      setActiveJob(null)
      await load()
    } catch (err) {
      setActionError(userFacingError(err))
    } finally {
      setDriveBusy(false)
    }
  }

  async function onSaveFolders() {
    setDriveBusy(true)
    setActionError(null)
    try {
      await driveApi.saveFolders(selectedFolders)
      setActionMsg("Folders saved.")
      await loadDrivePanel()
    } catch (err) {
      setActionError(userFacingError(err))
    } finally {
      setDriveBusy(false)
    }
  }

  async function onSyncNow() {
    setDriveBusy(true)
    setActionError(null)
    try {
      if (selectedFolders.length) {
        await driveApi.saveFolders(selectedFolders)
      }
      const job = await driveApi.sync({ folder_ids: selectedFolders, visibility: "org" })
      setActiveJob(job)
      setActionMsg("Sync started.")
      await load()
    } catch (err) {
      setActionError(userFacingError(err))
    } finally {
      setDriveBusy(false)
    }
  }

  function toggleFolder(id: string) {
    setSelectedFolders((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  async function onSubmitWorkspaceEnterprise(e: FormEvent) {
    e.preventDefault()
    setEntBusy(true)
    setActionError(null)
    try {
      const detail = await workspaceEnterpriseApi.submit(entDomain, entKey)
      setEntDetail(detail)
      setActionMsg(
        detail.status === "verified"
          ? "Google Workspace verified."
          : "Submitted, but verification did not pass — see the error below.",
      )
    } catch (err) {
      setActionError(userFacingError(err))
    } finally {
      setEntBusy(false)
    }
  }

  async function onDisableWorkspaceEnterprise() {
    if (!window.confirm("Disable the Google Workspace enterprise connection?")) return
    setEntBusy(true)
    setActionError(null)
    try {
      await workspaceEnterpriseApi.disable()
      setEntDetail(await workspaceEnterpriseApi.get())
      setActionMsg("Google Workspace connection disabled.")
    } catch (err) {
      setActionError(userFacingError(err))
    } finally {
      setEntBusy(false)
    }
  }

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <span className="eyebrow">CONNECTIONS</span>
          <h1>Connections</h1>
          <p>Connect the systems your business already uses.</p>
        </div>
      </div>
      {loading ? <LoadingState label="Loading connections..." /> : null}
      {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}
      {actionError ? <ErrorState message={actionError} /> : null}
      {actionMsg ? <p className="success-banner">{actionMsg}</p> : null}
      {!loading && !error ? (
        <div className="connector-grid">
          {connectors.map((connector) => {
            const canConnect =
              connector.enabled &&
              (connector.status === "available" ||
                connector.status === "disconnected" ||
                connector.status === "not_configured")
            const isDrive = connector.id === "google_drive"
            return (
              <article className="connector-card" key={connector.id}>
                <div className="connector-top">
                  <h2>{connector.name}</h2>
                  <span className={`status-pill status-${connector.status}`}>
                    {statusLabel(connector.status)}
                  </span>
                </div>
                <p>{connector.description}</p>
                {connector.account_email ? <small>Account {connector.account_email}</small> : null}
                {connector.last_sync_at ? (
                  <small>Last sync {formatDateTime(connector.last_sync_at)}</small>
                ) : null}
                {connector.document_count != null ? (
                  <small>{connector.document_count.toLocaleString()} documents</small>
                ) : null}
                {connector.failed_document_count ? (
                  <small>{connector.failed_document_count} failed</small>
                ) : null}
                {connector.health ? <small>Health: {connector.health}</small> : null}
                {connector.mode ? <small>Mode: {connector.mode}</small> : null}
                {connector.status === "not_implemented" ? (
                  <p className="muted">This connection isn't available yet.</p>
                ) : null}
                <div className="connector-actions">
                  {canConnect ? (
                    <button
                      type="button"
                      className="primary-button"
                      disabled={!canManage && isDrive}
                      onClick={() => void onConnect(connector.id)}
                    >
                      Connect
                    </button>
                  ) : (
                    <button type="button" className="secondary-button" disabled>
                      {statusLabel(connector.status)}
                    </button>
                  )}
                  {isDrive && driveConnected && canManage ? (
                    <button
                      type="button"
                      className="text-button"
                      disabled={driveBusy}
                      onClick={() => void onDisconnectDrive()}
                    >
                      Disconnect
                    </button>
                  ) : null}
                </div>
              </article>
            )
          })}
        </div>
      ) : null}

      {driveConnected && canManage && driveDetail ? (
        <section className="drive-panel">
          <div className="page-head">
            <div>
              <span className="eyebrow">GOOGLE DRIVE</span>
              <h2>Folders & sync</h2>
              <p>
                Choose folders to index. Permissions map conservatively into Vridhi ACL
                (domain → org, matched members → selected, otherwise private).
              </p>
            </div>
            <button
              type="button"
              className="primary-button"
              disabled={driveBusy || !selectedFolders.length}
              onClick={() => void onSyncNow()}
            >
              Sync Now
            </button>
          </div>

          {activeJob ? (
            <div className="sync-progress">
              <strong>Sync job</strong>
              <span className={`status-pill status-${activeJob.status}`}>{activeJob.status}</span>
              <p>
                {activeJob.progress_done ?? 0} done · {activeJob.progress_failed ?? 0} failed ·{" "}
                {activeJob.progress_skipped ?? 0} skipped / {activeJob.progress_total ?? 0} total
              </p>
              {activeJob.error_message ? <p className="muted">{activeJob.error_message}</p> : null}
            </div>
          ) : null}

          <div className="drive-folders">
            {folders.map((folder) => {
              const checked = selectedFolders.includes(folder.id)
              return (
                <label key={folder.id} className="folder-row">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleFolder(folder.id)}
                  />
                  <span>
                    <strong>{folder.name}</strong>
                    <small>{folder.path}</small>
                  </span>
                </label>
              )
            })}
          </div>
          <button
            type="button"
            className="secondary-button"
            disabled={driveBusy}
            onClick={() => void onSaveFolders()}
          >
            Save folders
          </button>

          {history.length ? (
            <div className="drive-history">
              <h3>Sync history</h3>
              <ul>
                {history.map((job) => (
                  <li key={job.id}>
                    <span className={`status-pill status-${job.status}`}>{job.status}</span>
                    <span>
                      {(job.progress_done ?? 0) + (job.progress_failed ?? 0)}/
                      {job.progress_total ?? 0} files · {formatDateTime(job.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {failedDocs.length ? (
            <div className="drive-failed">
              <h3>Failed documents</h3>
              <ul>
                {failedDocs.map((doc) => (
                  <li key={doc.id}>
                    <strong>{doc.title}</strong>
                    <span className="muted">{doc.error_message || "Ingest failed"}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      ) : null}

      {canManage ? (
        <section className="drive-panel">
          <div className="page-head">
            <div>
              <span className="eyebrow">ENTERPRISE</span>
              <h2>Google Workspace (Domain-Wide Delegation)</h2>
              <p>
                For organization-wide access: create a service account in your own
                Google Cloud project, authorize it for Domain-Wide Delegation in your
                Admin Console (scope: admin.directory.user.readonly), then paste the
                key below.
              </p>
            </div>
          </div>

          {entDetail?.status ? (
            <div className="sync-progress">
              <strong>Status</strong>
              <span className={`status-pill status-${entDetail.status}`}>{entDetail.status}</span>
              {entDetail.google_domain ? <p>Domain: {entDetail.google_domain}</p> : null}
              {entDetail.service_account_email ? (
                <p>Service account: {entDetail.service_account_email}</p>
              ) : null}
              {entDetail.last_error ? <p className="muted">{entDetail.last_error}</p> : null}
            </div>
          ) : null}

          <form onSubmit={(e) => void onSubmitWorkspaceEnterprise(e)}>
            <label>
              Google Workspace domain
              <input
                type="text"
                value={entDomain}
                onChange={(e) => setEntDomain(e.target.value)}
                placeholder="acme.com"
                required
              />
            </label>
            <label>
              Service account JSON key
              <textarea
                value={entKey}
                onChange={(e) => setEntKey(e.target.value)}
                rows={6}
                placeholder="Paste the full JSON key here"
                required
              />
            </label>
            <button type="submit" className="primary-button" disabled={entBusy}>
              Submit &amp; verify
            </button>
          </form>

          {entDetail?.connected ? (
            <button
              type="button"
              className="text-button"
              disabled={entBusy}
              onClick={() => void onDisableWorkspaceEnterprise()}
            >
              Disable
            </button>
          ) : null}
        </section>
      ) : null}
    </main>
  )
}

export function TeamPage() {
  const { user, membership } = useAuth()
  const [members, setMembers] = useState<Member[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<MemberRole>("member")
  const [inviteMsg, setInviteMsg] = useState<string | null>(null)
  const [inviteError, setInviteError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const canManage = membership?.role === "owner" || membership?.role === "admin"

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const memberRows = await teamApi.list()
      setMembers(memberRows)
      if (canManage) {
        try {
          setInvites(await teamApi.listInvites())
        } catch {
          setInvites([])
        }
      } else {
        setInvites([])
      }
    } catch (err) {
      setError(userFacingError(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function onInvite(e: FormEvent) {
    e.preventDefault()
    setInviteError(null)
    setInviteMsg(null)
    try {
      const invite = await teamApi.invite(email.trim(), role)
      setInviteMsg(
        invite.debug_token
          ? `Invitation created for ${email.trim()}. Local accept link: /invite/accept?token=${invite.debug_token}`
          : `Invitation sent to ${email.trim()}.`,
      )
      setEmail("")
      await load()
    } catch (err) {
      setInviteError(userFacingError(err))
    }
  }

  async function onChangeRole(member: Member, nextRole: MemberRole) {
    setActionError(null)
    try {
      await teamApi.patch(member.user_id, { role: nextRole })
      await load()
    } catch (err) {
      setActionError(userFacingError(err))
    }
  }

  async function onDeactivate(member: Member) {
    if (!window.confirm(`Deactivate ${member.name}? They will lose access to this workspace.`)) return
    setActionError(null)
    try {
      await teamApi.deactivate(member.user_id)
      await load()
    } catch (err) {
      setActionError(userFacingError(err))
    }
  }

  async function onResend(inviteId: string) {
    setActionError(null)
    try {
      const invite = await teamApi.resendInvite(inviteId)
      setInviteMsg(
        invite.debug_token
          ? `Invite resent. Local accept link: /invite/accept?token=${invite.debug_token}`
          : "Invite resent.",
      )
      await load()
    } catch (err) {
      setActionError(userFacingError(err))
    }
  }

  async function onRevoke(inviteId: string) {
    setActionError(null)
    try {
      await teamApi.revokeInvite(inviteId)
      await load()
    } catch (err) {
      setActionError(userFacingError(err))
    }
  }

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <span className="eyebrow">TEAM</span>
          <h1>Team</h1>
          <p>People in {membership?.organization_name ?? "your organization"}.</p>
        </div>
      </div>
      {loading ? <LoadingState label="Loading team..." /> : null}
      {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}
      {actionError ? <ErrorState message={actionError} /> : null}

      {canManage ? (
        <form className="invite-form ask-composer" onSubmit={onInvite}>
          <label htmlFor="invite-email">Invite a teammate</label>
          <div className="invite-row">
            <input id="invite-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
            <select value={role} onChange={(e) => setRole(e.target.value as MemberRole)} aria-label="Role">
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
            <button className="primary-button" type="submit">Invite</button>
          </div>
          {inviteMsg ? <p className="form-success">{inviteMsg}</p> : null}
          {inviteError ? <p className="form-error" role="alert">{inviteError}</p> : null}
        </form>
      ) : null}

      {!loading && !error && members.length === 0 ? (
        <EmptyState icon="users" title="No team members yet" description="Invite colleagues once you are ready to collaborate." />
      ) : null}

      {!loading && !error && members.length > 0 ? (
        <div className="data-table" role="table" aria-label="Team members">
          {members.map((member) => {
            const isSelf = member.user_id === user?.id
            const canEdit = canManage && !isSelf && member.role !== "owner" && member.status === "active"
            return (
              <div className="data-row team-row" role="row" key={member.id}>
                <div className="member-cell">
                  <span className="person-avatar">{initials(member.name)}</span>
                  <div>
                    <strong>{member.name}{isSelf ? " (you)" : ""}</strong>
                    <small>{member.email}</small>
                  </div>
                </div>
                {canEdit ? (
                  <select
                    aria-label={`Role for ${member.name}`}
                    value={member.role}
                    onChange={(e) => void onChangeRole(member, e.target.value as MemberRole)}
                  >
                    <option value="member">member</option>
                    <option value="admin">admin</option>
                  </select>
                ) : (
                  <span className="role-pill">{member.role}</span>
                )}
                <span className="muted">{member.status}</span>
                {canEdit ? (
                  <button type="button" className="text-button danger" onClick={() => void onDeactivate(member)}>
                    Deactivate
                  </button>
                ) : <span />}
              </div>
            )
          })}
        </div>
      ) : null}

      {canManage && invites.length > 0 ? (
        <section className="settings-card" style={{ marginTop: 18 }}>
          <h2>Pending invites</h2>
          <div className="data-table" role="table" aria-label="Pending invites">
            {invites.map((invite) => (
              <div className="data-row team-row" role="row" key={invite.id}>
                <div>
                  <strong>{invite.email}</strong>
                  <small>Expires {formatDateTime(invite.expires_at)}</small>
                </div>
                <span className="role-pill">{invite.role}</span>
                <button type="button" className="text-button" onClick={() => void onResend(invite.id)}>Resend</button>
                <button type="button" className="text-button danger" onClick={() => void onRevoke(invite.id)}>Revoke</button>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </main>
  )
}

export function UsagePage() {
  const [usage, setUsage] = useState<Usage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void (async () => {
      try {
        setUsage(await usageApi.get())
      } catch (err) {
        setError(userFacingError(err))
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <span className="eyebrow">USAGE</span>
          <h1>Usage</h1>
          <p>Workspace consumption for this organization.</p>
        </div>
      </div>
      {loading ? <LoadingState label="Loading usage..." /> : null}
      {error ? <ErrorState message={error} /> : null}
      {!loading && usage && !usage.available ? (
        <UnavailableState title="Usage data isn't available yet" description={usage.message || "Usage tracking is not enabled in this version."} />
      ) : null}
    </main>
  )
}

export function AuditPage() {
  const [data, setData] = useState<PaginatedAudit | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      setData(await auditApi.list())
    } catch (err) {
      setError(userFacingError(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <span className="eyebrow">AUDIT</span>
          <h1>Audit</h1>
          <p>Workspace activity and security events.</p>
        </div>
      </div>
      {loading ? <LoadingState label="Loading audit log..." /> : null}
      {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}
      {!loading && !error && data && data.total === 0 ? (
        <EmptyState icon="clock" title="No activity recorded yet" description="Login, invites, and connection events will appear here." />
      ) : null}
      {!loading && !error && data && data.items.length > 0 ? (
        <div className="data-table" role="table" aria-label="Audit events">
          {data.items.map((item) => (
            <div className="data-row" role="row" key={item.id}>
              <div>
                <strong>{formatAction(item.action)}</strong>
                <small>{item.request_id ? `Request ${item.request_id}` : "No request id"}</small>
              </div>
              <time>{formatDateTime(item.created_at)}</time>
            </div>
          ))}
        </div>
      ) : null}
    </main>
  )
}

export function SettingsPage() {
  const { user, membership, logout } = useAuth()

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <span className="eyebrow">SETTINGS</span>
          <h1>Settings</h1>
          <p>Account and organization preferences that are available today.</p>
        </div>
      </div>
      <section className="settings-card">
        <h2>Profile</h2>
        <dl className="settings-dl">
          <div><dt>Name</dt><dd>{user?.name}</dd></div>
          <div><dt>Email</dt><dd>{user?.email}</dd></div>
          <div><dt>Email verified</dt><dd>{user?.email_verified_at ? formatDateTime(user.email_verified_at) : "Not verified"}</dd></div>
          <div><dt>Role</dt><dd>{membership?.role ?? "—"}</dd></div>
          <div><dt>Organization</dt><dd>{membership?.organization_name ?? "Organization information unavailable."}</dd></div>
        </dl>
        <button type="button" className="secondary-button" onClick={() => void logout()}>Sign out</button>
      </section>
      <section className="settings-card">
        <h2>Security</h2>
        <p className="muted">Password reset is available from the sign-in page. Google login appears on sign-in only when configured on the server. SSO isn't available in this version.</p>
      </section>
      <section className="settings-card">
        <h2>Billing</h2>
        <p className="muted">Billing management isn't available in this version.</p>
      </section>
    </main>
  )
}
