import { ApiError, API_BASE, apiRequest, apiUpload } from "@/api/client"
import type {
  AuditEvent,
  ChatConnection,
  ChatMessage,
  ChatSpace,
  Citation,
  Connector,
  Dashboard,
  DocumentItem,
  DocumentPreview,
  DriveConnection,
  DriveFolder,
  GmailConnection,
  Invite,
  Member,
  MemberRole,
  MemberStatus,
  PaginatedAudit,
  PaginatedDocuments,
  SearchResponse,
  SyncJob,
  UploadResult,
  Usage,
  ApiErrorBody,
  WorkspaceEnterpriseStatus,
} from "@/types"

export const dashboardApi = {
  get() {
    return apiRequest<Dashboard>("/v1/dashboard")
  },
}

export const connectorsApi = {
  list() {
    return apiRequest<{ connectors: Connector[] }>("/v1/connectors")
  },
  connect(connectorId: string) {
    return apiRequest<{ ok?: boolean; oauth_start_path?: string }>(
      `/v1/connections/${connectorId}/connect`,
      { method: "POST" },
    )
  },
}

export const driveApi = {
  get() {
    return apiRequest<DriveConnection>("/v1/connections/google_drive")
  },
  oauthStartUrl() {
    return `${API_BASE}/v1/connections/google_drive/oauth/start`
  },
  disconnect() {
    return apiRequest<{ ok: boolean }>("/v1/connections/google_drive", { method: "DELETE" })
  },
  folders() {
    return apiRequest<{ folders: DriveFolder[]; selected_folder_ids: string[] }>(
      "/v1/connections/google_drive/folders",
    )
  },
  saveFolders(folderIds: string[]) {
    return apiRequest<DriveConnection>("/v1/connections/google_drive/folders", {
      method: "PUT",
      body: { folder_ids: folderIds },
    })
  },
  sync(body: {
    folder_ids?: string[]
    visibility?: "private" | "org" | "selected"
    incremental?: boolean
  } = {}) {
    return apiRequest<SyncJob>("/v1/connections/google_drive/sync", {
      method: "POST",
      body: {
        folder_ids: body.folder_ids,
        visibility: body.visibility || "org",
        incremental: body.incremental ?? true,
        selected_user_ids: [],
      },
    })
  },
  syncHistory(limit = 20) {
    return apiRequest<{ items: SyncJob[] }>(`/v1/connections/google_drive/syncs?limit=${limit}`)
  },
  failedDocuments(limit = 50) {
    return apiRequest<{ items: DocumentItem[] }>(
      `/v1/connections/google_drive/failed-documents?limit=${limit}`,
    )
  },
  setAutoSync(enabled: boolean) {
    return apiRequest<DriveConnection>("/v1/connections/google_drive/auto-sync", {
      method: "PUT",
      body: { enabled },
    })
  },
}

export const gmailApi = {
  disconnect() {
    return apiRequest<{ ok: boolean }>("/v1/connections/gmail", { method: "DELETE" })
  },
  sync(body: { query?: string; incremental?: boolean } = {}) {
    return apiRequest<SyncJob>("/v1/connections/gmail/sync", {
      method: "POST",
      body: {
        query: body.query,
        incremental: body.incremental ?? true,
      },
    })
  },
  setAutoSync(enabled: boolean) {
    return apiRequest<GmailConnection>("/v1/connections/gmail/auto-sync", {
      method: "PUT",
      body: { enabled },
    })
  },
}

export const chatApi = {
  get() {
    return apiRequest<ChatConnection>("/v1/connections/google_chat")
  },
  oauthStartUrl() {
    return `${API_BASE}/v1/connections/google_chat/oauth/start`
  },
  disconnect() {
    return apiRequest<{ ok: boolean }>("/v1/connections/google_chat", { method: "DELETE" })
  },
  spaces() {
    return apiRequest<{ spaces: ChatSpace[]; selected_space_ids: string[] }>(
      "/v1/connections/google_chat/spaces",
    )
  },
  saveSpaces(spaceIds: string[]) {
    return apiRequest<ChatConnection>("/v1/connections/google_chat/spaces", {
      method: "PUT",
      body: { space_ids: spaceIds },
    })
  },
  sync(body: { space_ids?: string[]; incremental?: boolean } = {}) {
    return apiRequest<SyncJob>("/v1/connections/google_chat/sync", {
      method: "POST",
      body: { space_ids: body.space_ids, incremental: body.incremental ?? true },
    })
  },
  setAutoSync(enabled: boolean) {
    return apiRequest<ChatConnection>("/v1/connections/google_chat/auto-sync", {
      method: "PUT",
      body: { enabled },
    })
  },
}

export const workspaceEnterpriseApi = {
  get() {
    return apiRequest<WorkspaceEnterpriseStatus>("/v1/enterprise/google-workspace")
  },
  submit(googleDomain: string, serviceAccountKey: string) {
    return apiRequest<WorkspaceEnterpriseStatus>("/v1/enterprise/google-workspace", {
      method: "POST",
      body: { google_domain: googleDomain, service_account_key: serviceAccountKey },
    })
  },
  verify() {
    return apiRequest<WorkspaceEnterpriseStatus>("/v1/enterprise/google-workspace/verify", {
      method: "POST",
    })
  },
  disable() {
    return apiRequest<{ ok: boolean }>("/v1/enterprise/google-workspace", { method: "DELETE" })
  },
}

export const documentsApi = {
  list(page = 1, limit = 20) {
    return apiRequest<PaginatedDocuments>(`/v1/documents?page=${page}&limit=${limit}`)
  },
  get(id: string) {
    return apiRequest<DocumentItem>(`/v1/documents/${id}`)
  },
  delete(id: string) {
    return apiRequest<{ ok: boolean }>(`/v1/documents/${id}`, { method: "DELETE" })
  },
  preview(id: string) {
    return apiRequest<DocumentPreview>(`/v1/documents/${id}/preview`)
  },
  job(id: string) {
    return apiRequest<SyncJob>(`/v1/documents/${id}/job`)
  },
  upload(file: File, visibility: "private" | "org" | "selected", selectedUserIds: string[] = []) {
    const form = new FormData()
    form.append("file", file)
    form.append("visibility", visibility)
    if (selectedUserIds.length) {
      form.append("selected_user_ids", selectedUserIds.join(","))
    }
    return apiUpload<UploadResult>("/v1/documents/upload", form)
  },
}

export const jobsApi = {
  get(jobId: string) {
    return apiRequest<SyncJob>(`/v1/jobs/${jobId}`)
  },
}

export const teamApi = {
  list() {
    return apiRequest<Member[]>("/v1/users")
  },
  listInvites() {
    return apiRequest<Invite[]>("/v1/users/invites")
  },
  invite(email: string, role: MemberRole) {
    return apiRequest<Invite>("/v1/users/invite", { method: "POST", body: { email, role } })
  },
  resendInvite(inviteId: string) {
    return apiRequest<Invite>(`/v1/users/invites/${inviteId}/resend`, { method: "POST" })
  },
  revokeInvite(inviteId: string) {
    return apiRequest<{ ok: boolean }>(`/v1/users/invites/${inviteId}`, { method: "DELETE" })
  },
  patch(userId: string, body: { role?: MemberRole; status?: MemberStatus }) {
    return apiRequest<Member>(`/v1/users/${userId}`, { method: "PATCH", body })
  },
  deactivate(userId: string) {
    return apiRequest<{ ok: boolean }>(`/v1/users/${userId}`, { method: "DELETE" })
  },
}

export const auditApi = {
  list(page = 1, limit = 20) {
    return apiRequest<PaginatedAudit>(`/v1/audit?page=${page}&limit=${limit}`)
  },
}

export const usageApi = {
  get() {
    return apiRequest<Usage>("/v1/usage")
  },
}

export const intelligenceApi = {
  chat(query: string, conversationId?: string | null) {
    return apiRequest<{
      conversation_id: string
      user_message: ChatMessage
      assistant_message: ChatMessage
    }>("/v1/chat", {
      method: "POST",
      body: { query, conversation_id: conversationId || null, stream: false },
    })
  },
  search(query: string, limit = 10) {
    return apiRequest<SearchResponse>("/v1/search", { method: "POST", body: { query, limit } })
  },
  feedback(messageId: string, rating: "up" | "down", comment?: string) {
    return apiRequest<{ id: string; rating: string }>(`/v1/messages/${messageId}/feedback`, {
      method: "POST",
      body: { rating, comment },
    })
  },
  async chatStream(
    input: { query: string; conversation_id?: string | null; stream?: boolean },
    handlers: {
      onMeta: (meta: {
        conversation_id: string
        user_message_id: string
        assistant_message_id: string
        no_answer: boolean
        model: string | null
      }) => void
      onCitation: (citation: Citation) => void
      onToken: (text: string) => void
      onNoAnswer: (payload: { message: string; message_id: string }) => void
      onDone: (payload: { conversation_id: string; message_id: string; content: string }) => void
      onError: (err: unknown) => void
    },
  ) {
    let response: Response
    try {
      response = await fetch(`${API_BASE}/v1/chat`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          query: input.query,
          conversation_id: input.conversation_id || null,
          stream: true,
        }),
      })
    } catch {
      handlers.onError(new ApiError(0, null, "Unable to reach Vridhi. Check that the API is running."))
      return
    }

    if (!response.ok) {
      const text = await response.text()
      let json: unknown = null
      try {
        json = text ? JSON.parse(text) : null
      } catch {
        json = null
      }
      handlers.onError(new ApiError(response.status, json as ApiErrorBody | null))
      return
    }

    // Non-stream JSON fallback
    const contentType = response.headers.get("content-type") || ""
    if (contentType.includes("application/json")) {
      const data = (await response.json()) as {
        conversation_id: string
        user_message: ChatMessage
        assistant_message: ChatMessage
      }
      handlers.onMeta({
        conversation_id: data.conversation_id,
        user_message_id: data.user_message.id,
        assistant_message_id: data.assistant_message.id,
        no_answer: data.assistant_message.no_answer,
        model: data.assistant_message.model,
      })
      for (const c of data.assistant_message.citations || []) handlers.onCitation(c)
      if (data.assistant_message.no_answer) {
        handlers.onNoAnswer({ message: data.assistant_message.content, message_id: data.assistant_message.id })
      } else {
        handlers.onToken(data.assistant_message.content)
        handlers.onDone({
          conversation_id: data.conversation_id,
          message_id: data.assistant_message.id,
          content: data.assistant_message.content,
        })
      }
      return
    }

    if (!response.body) {
      handlers.onError(new Error("Empty stream"))
      return
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const chunks = buffer.split("\n\n")
      buffer = chunks.pop() || ""
      for (const block of chunks) {
        const lines = block.split("\n")
        let event = "message"
        let dataLine = ""
        for (const line of lines) {
          if (line.startsWith("event:")) event = line.slice(6).trim()
          if (line.startsWith("data:")) dataLine += line.slice(5).trim()
        }
        if (!dataLine) continue
        const data = JSON.parse(dataLine)
        if (event === "meta") handlers.onMeta(data)
        else if (event === "citation") handlers.onCitation(data)
        else if (event === "token") handlers.onToken(data.text)
        else if (event === "no_answer") handlers.onNoAnswer(data)
        else if (event === "done") handlers.onDone(data)
      }
    }
  },
}

export type { DocumentItem, AuditEvent }
