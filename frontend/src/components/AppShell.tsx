import { NavLink, Outlet, useNavigate } from "react-router-dom"
import { useAuth } from "@/auth/AuthContext"
import { Icon, type IconName } from "@/components/Icon"
import { initials } from "@/utils/format"

const nav: { to: string; label: string; icon: IconName; short: string }[] = [
  { to: "/app/dashboard", label: "Dashboard", icon: "grid", short: "Home" },
  { to: "/app/ask", label: "Ask Vridhi", icon: "spark", short: "Ask" },
  { to: "/app/search", label: "Search", icon: "search", short: "Search" },
  { to: "/app/knowledge", label: "Knowledge", icon: "book", short: "Docs" },
  { to: "/app/connections", label: "Connections", icon: "link", short: "Connect" },
  { to: "/app/team", label: "Team", icon: "users", short: "Team" },
  { to: "/app/usage", label: "Usage", icon: "chart", short: "Usage" },
  { to: "/app/audit", label: "Audit", icon: "clock", short: "Audit" },
]

function Logo() {
  return (
    <div className="brand">
      <span className="brand-mark"><i /><i /><i /></span>
      <span>vridhi<span className="brand-dot">.ai</span></span>
    </div>
  )
}

export function AppShell() {
  const { user, membership, logout } = useAuth()
  const navigate = useNavigate()

  if (!user) return null

  const orgName = membership?.organization_name ?? "No organization"
  const orgInitial = orgName.trim().charAt(0).toUpperCase() || "?"
  const roleLabel = membership?.role ? membership.role.charAt(0).toUpperCase() + membership.role.slice(1) : "Member"

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Logo />
        <div className="workspace" title={orgName}>
          <span className="workspace-logo">{orgInitial}</span>
          <span className="workspace-name">{orgName}</span>
        </div>
        <nav aria-label="Main navigation" className="nav-list">
          {nav.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "nav-item active" : "nav-item")}>
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <NavLink to="/app/settings" className={({ isActive }) => (isActive ? "nav-item active" : "nav-item")}>
            <Icon name="settings" />
            <span>Settings</span>
          </NavLink>
          <div className="profile">
            <span className="avatar">{initials(user.name)}</span>
            <span>
              <strong>{user.name}</strong>
              <small>{roleLabel}</small>
            </span>
            <button
              type="button"
              className="icon-button"
              aria-label="Sign out"
              onClick={async () => {
                await logout()
                navigate("/login")
              }}
            >
              <Icon name="close" size={15} />
            </button>
          </div>
        </div>
      </aside>
      <div className="content-area">
        <Outlet />
      </div>
      <nav className="mobile-nav" aria-label="Mobile navigation">
        {nav.slice(0, 5).map((item) => (
          <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "mobile-active" : undefined)}>
            <Icon name={item.icon} />
            <span>{item.short}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
