export type MemberRole = "owner" | "admin" | "member"
export type MemberStatus = "active" | "deactivated"

export type ConnectorStatus =
  | "available"
  | "connected"
  | "disconnected"
  | "connecting"
  | "syncing"
  | "sync_failed"
  | "auth_required"
  | "not_configured"
  | "not_implemented"

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    request_id: string | null
  }
}

export interface User {
  id: string
  email: string
  name: string
  email_verified_at: string | null
  status: string
}

export interface Membership {
  tenant_id: string
  role: MemberRole
  status: MemberStatus
  organization_name: string
  organization_slug: string
}

export interface AuthSession {
  user: User
  membership: Membership | null
}

export interface Organization {
  id: string
  name: string
  slug: string
  created_at: string
}

export interface Member {
  id: string
  user_id: string
  email: string
  name: string
  role: MemberRole
  status: MemberStatus
  created_at: string
}

export interface Invite {
  id: string
  email: string
  role: MemberRole
  status: string
  expires_at: string
  created_at: string
  debug_token?: string | null
}

export interface Connector {
  id: string
  name: string
  description: string
  status: ConnectorStatus
  enabled: boolean
  last_sync_at: string | null
  document_count: number | null
  failed_document_count?: number | null
  health: string | null
  account_email?: string | null
  mode?: string | null
}

export interface DashboardActivity {
  id: string
  action: string
  created_at: string
  user_id: string | null
  metadata: Record<string, unknown>
}

export interface Dashboard {
  documents: number
  chunks: number
  connections_connected: number
  connections_available: number
  questions: number
  users: number
  last_sync_at: string | null
  recent_activity: DashboardActivity[]
}

export interface DocumentItem {
  id: string
  title: string
  mime_type: string | null
  source: string | null
  status: string
  visibility: string
  uploaded_by_user_id?: string | null
  job_id?: string | null
  job_status?: string | null
  error_message?: string | null
  byte_size?: number | null
  granted_user_ids?: string[]
  created_at?: string | null
  updated_at: string
}

export interface SyncJob {
  id: string
  connection_id?: string | null
  document_id: string | null
  version_id: string | null
  job_type: string
  status: string
  attempt: number
  max_attempts: number
  progress_total?: number
  progress_done?: number
  progress_failed?: number
  progress_skipped?: number
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

export interface DriveConnection {
  connected: boolean
  status: string
  health: string | null
  account_email: string | null
  last_sync_at: string | null
  last_error: string | null
  document_count: number
  failed_document_count: number
  selected_folder_ids: string[]
  mode: string
  connection_id: string | null
}

export interface DriveFolder {
  id: string
  name: string
  path: string
}

export interface UploadResult {
  document: DocumentItem
  job: SyncJob
}

export interface DocumentPreview {
  document_id: string
  title: string
  status: string
  preview: string
  truncated: boolean
  chunk_count: number
}

export interface PaginatedDocuments {
  items: DocumentItem[]
  total: number
  page: number
  limit: number
}

export interface AuditEvent {
  id: string
  action: string
  user_id: string | null
  request_id: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export interface PaginatedAudit {
  items: AuditEvent[]
  total: number
  page: number
  limit: number
}

export interface Features {
  google_login_enabled: boolean
  google_drive_enabled: boolean
  gmail_enabled: boolean
  file_upload_enabled: boolean
  ai_query_enabled: boolean
  search_enabled: boolean
  audit_enabled: boolean
  usage_enabled: boolean
  billing_enabled: boolean
  sso_enabled: boolean
  upload_max_bytes?: number | null
  upload_allowed_extensions?: string[]
  chat_stream_enabled?: boolean
  llm_provider?: string | null
}

export interface Citation {
  id: string
  rank: number
  document_id: string | null
  chunk_id: string | null
  title: string
  passage: string
  page: number | null
  chunk_index: number | null
  score: number | null
  source_url: string | null
}

export interface ChatMessage {
  id: string
  role: string
  content: string
  no_answer: boolean
  model: string | null
  created_at: string
  citations: Citation[]
  feedback?: string | null
}

export interface SearchHit {
  document_id: string
  chunk_id: string
  title: string
  preview: string
  score: number
  page: number | null
  chunk_index: number | null
  source_url: string | null
}

export interface SearchResponse {
  query: string
  total: number
  results: SearchHit[]
}

export interface Usage {
  available: boolean
  message: string | null
  users: number | null
  questions: number | null
  documents: number | null
  storage_bytes: number | null
}

