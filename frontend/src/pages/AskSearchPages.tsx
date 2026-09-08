import { FormEvent, useEffect, useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { ApiError, userFacingError } from "@/api/client"
import { featuresApi } from "@/api/auth"
import { intelligenceApi } from "@/api/workspace"
import { EmptyState, LoadingState, UnavailableState } from "@/components/States"
import { Icon } from "@/components/Icon"
import type { Citation, Features, SearchHit } from "@/types"

type AskState = "idle" | "submitting" | "streaming" | "answered" | "no_answer" | "unavailable" | "error"

export function AskPage() {
  const [query, setQuery] = useState("")
  const [state, setState] = useState<AskState>("idle")
  const [message, setMessage] = useState<string | null>(null)
  const [answer, setAnswer] = useState("")
  const [citations, setCitations] = useState<Citation[]>([])
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [assistantMessageId, setAssistantMessageId] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null)
  const [features, setFeatures] = useState<Features | null>(null)

  useEffect(() => {
    void featuresApi.get().then(setFeatures).catch(() => undefined)
  }, [])

  const aiEnabled = features?.ai_query_enabled ?? true

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    const q = query.trim()
    if (!q) return
    if (features && !features.ai_query_enabled) {
      setState("unavailable")
      setMessage("AI search isn't available yet.")
      return
    }

    setState("submitting")
    setMessage(null)
    setAnswer("")
    setCitations([])
    setActiveCitation(null)
    setAssistantMessageId(null)
    setFeedback(null)

    try {
      await intelligenceApi.chatStream(
        { query: q, conversation_id: conversationId, stream: true },
        {
          onMeta(meta) {
            setConversationId(meta.conversation_id)
            setAssistantMessageId(meta.assistant_message_id)
            setState(meta.no_answer ? "no_answer" : "streaming")
          },
          onCitation(citation) {
            setCitations((prev) => [...prev, citation].sort((a, b) => a.rank - b.rank))
          },
          onToken(text) {
            setState("streaming")
            setAnswer((prev) => prev + text)
          },
          onNoAnswer(payload) {
            setState("no_answer")
            setAnswer(payload.message)
            setMessage(payload.message)
          },
          onDone(payload) {
            setState(payload.content ? "answered" : "no_answer")
            if (payload.content) setAnswer(payload.content)
          },
          onError(err) {
            throw err
          },
        },
      )
      setState((prev) => (prev === "streaming" ? "answered" : prev))
    } catch (err) {
      if (err instanceof ApiError && (err.status === 501 || err.code === "AI_NOT_AVAILABLE")) {
        setState("unavailable")
        setMessage(err.message || "AI search isn't available yet.")
        return
      }
      setState("error")
      setMessage(userFacingError(err))
    }
  }

  async function sendFeedback(rating: "up" | "down") {
    if (!assistantMessageId) return
    try {
      await intelligenceApi.feedback(assistantMessageId, rating)
      setFeedback(rating)
    } catch (err) {
      setMessage(userFacingError(err))
    }
  }

  const renderedAnswer = useMemo(() => renderAnswerWithCitations(answer, citations, setActiveCitation), [answer, citations])

  return (
    <main className={`page ask-page ${activeCitation ? "ask-with-drawer" : ""}`}>
      <div className="page-head">
        <div>
          <span className="eyebrow">KNOWLEDGE ASSISTANT</span>
          <h1>Ask Vridhi</h1>
          <p>Answers are grounded only in your connected company sources.</p>
        </div>
      </div>

      <form className="ask-composer" onSubmit={onSubmit}>
        <label htmlFor="question">Ask anything about your business</label>
        <div className="textarea-wrap">
          <textarea
            id="question"
            rows={2}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={aiEnabled ? "e.g. What was Q2 revenue growth?" : "AI isn't enabled yet"}
            disabled={!aiEnabled && Boolean(features)}
          />
          <button type="submit" aria-label="Ask Vridhi" disabled={state === "submitting" || state === "streaming" || !query.trim()}>
            <Icon name="arrow" size={19} />
          </button>
        </div>
        <div className="composer-foot">
          <span>
            {state === "submitting" || state === "streaming"
              ? "Retrieving and answering…"
              : "Uses only indexed company knowledge · citations required"}
          </span>
          <Link to="/app/knowledge">Knowledge</Link>
        </div>
      </form>

      {state === "idle" && !answer ? (
        <EmptyState
          icon="spark"
          title="Ask when your knowledge is ready"
          description="Upload documents first. Vridhi will not invent answers when evidence is missing."
          action={<Link className="primary-button" to="/app/knowledge">Go to Knowledge</Link>}
        />
      ) : null}

      {state === "unavailable" ? (
        <UnavailableState title="AI search isn't available yet" description={message || "Enable AI_QUERY_ENABLED and OpenSearch."} />
      ) : null}

      {state === "error" ? (
        <UnavailableState title="We couldn't complete your request" description={message || "Please try again."} />
      ) : null}

      {(state === "streaming" || state === "answered" || state === "no_answer") && answer ? (
        <section className="answer">
          <div className="answer-top">
            <div className="answer-ident">
              <span className="answer-spark"><Icon name="spark" size={14} /></span>
              <strong>Vridhi</strong>
              <span className="answer-label">{state === "no_answer" ? "NO ANSWER" : "GROUNDED"}</span>
            </div>
            {assistantMessageId && state !== "streaming" ? (
              <div className="feedback-row">
                <button type="button" className={`text-button ${feedback === "up" ? "active" : ""}`} onClick={() => void sendFeedback("up")} aria-label="Helpful">👍</button>
                <button type="button" className={`text-button ${feedback === "down" ? "active" : ""}`} onClick={() => void sendFeedback("down")} aria-label="Not helpful">👎</button>
              </div>
            ) : null}
          </div>
          <div className="answer-content">{renderedAnswer}</div>
          {citations.length > 0 ? (
            <div className="answer-evidence">
              <span className="evidence-label">Sources</span>
              {citations.map((c) => (
                <button key={c.id} type="button" className="citation" onClick={() => setActiveCitation(c)}>
                  [{c.rank}] {c.title}
                </button>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {state === "submitting" ? <LoadingState label="Searching your knowledge…" /> : null}

      {activeCitation ? (
        <aside className="source-drawer" aria-label="Citation details">
          <header>
            <div>
              <span className="eyebrow">SOURCE [{activeCitation.rank}]</span>
              <h2>{activeCitation.title}</h2>
            </div>
            <button type="button" className="icon-button" onClick={() => setActiveCitation(null)} aria-label="Close">
              <Icon name="close" size={16} />
            </button>
          </header>
          <div className="document-chip">
            <span className="pdf-icon">DOC</span>
            <div>
              <strong>{activeCitation.title}</strong>
              <small>
                {activeCitation.page != null ? `Page ${activeCitation.page}` : `Passage ${activeCitation.chunk_index ?? activeCitation.rank}`}
                {activeCitation.score != null ? ` · score ${activeCitation.score.toFixed(3)}` : ""}
              </small>
            </div>
          </div>
          <div className="source-meta">
            <span>Evidence passage</span>
            {activeCitation.source_url ? (
              <a href={activeCitation.source_url} target="_blank" rel="noreferrer">Open original</a>
            ) : (
              <span className="muted">No external URL</span>
            )}
          </div>
          <div className="passage">
            <p>{activeCitation.passage}</p>
          </div>
        </aside>
      ) : null}
    </main>
  )
}

function renderAnswerWithCitations(
  answer: string,
  citations: Citation[],
  onOpen: (c: Citation) => void,
) {
  if (!answer) return null
  const parts = answer.split(/(\[\d+\])/g)
  return (
    <p>
      {parts.map((part, idx) => {
        const match = part.match(/^\[(\d+)\]$/)
        if (!match) return <span key={idx}>{part}</span>
        const rank = Number(match[1])
        const cite = citations.find((c) => c.rank === rank)
        if (!cite) return <span key={idx}>{part}</span>
        return (
          <button key={idx} type="button" className="inline-cite" onClick={() => onOpen(cite)}>
            [{rank}]
          </button>
        )
      })}
    </p>
  )
}

export function SearchPage() {
  const [query, setQuery] = useState("")
  const [state, setState] = useState<"idle" | "loading" | "unavailable" | "empty" | "error" | "results">("idle")
  const [message, setMessage] = useState<string | null>(null)
  const [results, setResults] = useState<SearchHit[]>([])
  const [active, setActive] = useState<SearchHit | null>(null)
  const [features, setFeatures] = useState<Features | null>(null)

  useEffect(() => {
    void featuresApi.get().then(setFeatures).catch(() => undefined)
  }, [])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    const q = query.trim()
    if (!q) return
    if (features && !features.search_enabled) {
      setState("unavailable")
      setMessage("Search isn't available yet.")
      return
    }
    setState("loading")
    setMessage(null)
    setActive(null)
    try {
      const res = await intelligenceApi.search(q)
      setResults(res.results)
      setState(res.results.length ? "results" : "empty")
    } catch (err) {
      if (err instanceof ApiError && (err.status === 501 || err.code === "SEARCH_NOT_AVAILABLE")) {
        setState("unavailable")
        setMessage(err.message)
        return
      }
      setState("error")
      setMessage(userFacingError(err))
    }
  }

  return (
    <main className={`page ${active ? "ask-with-drawer" : ""}`}>
      <div className="page-head">
        <div>
          <span className="eyebrow">SEARCH</span>
          <h1>Search</h1>
          <p>Hybrid BM25 + vector results from your indexed company knowledge.</p>
        </div>
      </div>
      <form className="ask-composer" onSubmit={onSubmit}>
        <label htmlFor="search">Search company knowledge</label>
        <div className="textarea-wrap">
          <textarea id="search" rows={1} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search documents and passages" />
          <button type="submit" aria-label="Search" disabled={state === "loading" || !query.trim()}><Icon name="search" size={18} /></button>
        </div>
      </form>
      {state === "idle" ? <EmptyState icon="search" title="Search your company knowledge" description="Results will appear here once documents are indexed." /> : null}
      {state === "loading" ? <LoadingState label="Searching…" /> : null}
      {state === "unavailable" ? <UnavailableState title="Search isn't available yet" description={message || "Enable SEARCH_ENABLED and OpenSearch."} /> : null}
      {state === "empty" ? <EmptyState icon="search" title="No results found" description="Try another query once more documents are connected." /> : null}
      {state === "error" ? <UnavailableState title="Search failed" description={message || "Please try again."} /> : null}
      {state === "results" ? (
        <div className="data-table" role="list">
          {results.map((hit) => (
            <button key={`${hit.document_id}-${hit.chunk_id}`} type="button" className="data-row search-hit" onClick={() => setActive(hit)}>
              <div>
                <strong>{hit.title}</strong>
                <small>{hit.preview}</small>
              </div>
              <span className="muted">{hit.page != null ? `p.${hit.page}` : `#${hit.chunk_index}`} · {hit.score.toFixed(3)}</span>
            </button>
          ))}
        </div>
      ) : null}

      {active ? (
        <aside className="source-drawer" aria-label="Search preview">
          <header>
            <div>
              <span className="eyebrow">PREVIEW</span>
              <h2>{active.title}</h2>
            </div>
            <button type="button" className="icon-button" onClick={() => setActive(null)} aria-label="Close">
              <Icon name="close" size={16} />
            </button>
          </header>
          <div className="source-meta">
            <span>{active.page != null ? `Page ${active.page}` : `Chunk ${active.chunk_index}`}</span>
            {active.source_url ? (
              <a href={active.source_url} target="_blank" rel="noreferrer">Open original</a>
            ) : (
              <span className="muted">No external URL</span>
            )}
          </div>
          <div className="passage">
            <p>{active.preview}</p>
          </div>
        </aside>
      ) : null}
    </main>
  )
}
