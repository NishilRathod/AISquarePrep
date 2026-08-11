import { NavLink } from "react-router-dom";
import { HealthPill } from "./HealthPill";

interface SiteHeaderProps {
  title: string;
  subtitle: string;
  /** Page-specific control, e.g. Home's Refresh button. */
  action?: React.ReactNode;
}

const LINKS = [
  { to: "/", label: "Home", end: true },
  { to: "/anomalies", label: "Anomalies", end: false },
];

export function SiteHeader({ title, subtitle, action }: SiteHeaderProps) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-0.5 text-[13px]" style={{ color: "var(--color-ink-muted)" }}>
          {subtitle}
        </p>
      </div>

      <div className="flex items-center gap-3">
        <nav aria-label="Sections" className="flex items-center gap-1">
          {LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className="btn btn-ghost text-[13px]"
              style={({ isActive }) =>
                isActive
                  ? {
                      borderColor: "var(--color-accent)",
                      color: "var(--color-accent-strong)",
                    }
                  : undefined
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>

        <HealthPill />
        {action}
      </div>
    </header>
  );
}
