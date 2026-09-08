import { Icon, type IconName } from "@/components/Icon"

export function EmptyState({
  icon = "book",
  title,
  description,
  action,
}: {
  icon?: IconName
  title: string
  description: string
  action?: React.ReactNode
}) {
  return (
    <section className="empty-surface" role="status">
      <span className="empty-icon"><Icon name={icon} /></span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action ? <div className="empty-actions">{action}</div> : null}
    </section>
  )
}

export function LoadingState({ label }: { label: string }) {
  return (
    <div className="state-banner loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state-banner error" role="alert">
      <Icon name="alert" />
      <span>{message}</span>
      {onRetry ? <button type="button" className="text-button" onClick={onRetry}>Try again</button> : null}
    </div>
  )
}

export function UnavailableState({ title, description }: { title: string; description: string }) {
  return (
    <section className="empty-surface" role="status">
      <span className="empty-icon"><Icon name="alert" /></span>
      <h2>{title}</h2>
      <p>{description}</p>
    </section>
  )
}
