import type { ApiErrorBody } from "@/types"

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") || "http://127.0.0.1:8001"

export class ApiError extends Error {
  code: string
  status: number
  requestId: string | null

  constructor(status: number, body: ApiErrorBody | null, fallback = "Something went wrong.") {
    const message = body?.error?.message || fallback
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = body?.error?.code || "UNKNOWN"
    this.requestId = body?.error?.request_id ?? null
  }
}

export function userFacingError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Your session has expired. Please sign in again."
    if (err.status === 403) return "You don't have permission to access this resource."
    if (err.status === 429) return "Too many requests. Please wait a moment and try again."
    if (err.status >= 500 && err.code !== "AI_NOT_AVAILABLE" && err.code !== "SEARCH_NOT_AVAILABLE") {
      return "We couldn't complete your request. Please try again."
    }
    return err.message
  }
  return "We couldn't complete your request. Please try again."
}

type RequestOptions = {
  method?: string
  body?: unknown
  signal?: AbortSignal
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal } = options
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      credentials: "include",
      headers: body !== undefined ? { "Content-Type": "application/json", Accept: "application/json" } : { Accept: "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    })
  } catch {
    throw new ApiError(0, null, "Unable to reach Vridhi. Check that the API is running.")
  }

  if (response.status === 204) {
    return undefined as T
  }

  const text = await response.text()
  let json: unknown = null
  if (text) {
    try {
      json = JSON.parse(text)
    } catch {
      json = null
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, json as ApiErrorBody | null)
  }

  return json as T
}

export async function apiUpload<T>(path: string, formData: FormData, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json" },
      body: formData,
      signal,
    })
  } catch {
    throw new ApiError(0, null, "Unable to reach Vridhi. Check that the API is running.")
  }

  const text = await response.text()
  let json: unknown = null
  if (text) {
    try {
      json = JSON.parse(text)
    } catch {
      json = null
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, json as ApiErrorBody | null)
  }

  return json as T
}

export function googleLoginUrl(): string {
  return `${API_BASE}/v1/auth/google`
}

export { API_BASE }
