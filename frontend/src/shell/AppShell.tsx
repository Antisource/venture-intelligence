import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { healthQuery } from "../api/queries";
import { Icon } from "../components/Icon";
import { usePrograms } from "../state/ProgramProvider";
import { useTheme, type ThemePreference } from "../theme/ThemeProvider";
import { CommandPalette } from "./CommandPalette";
import { navigationItems } from "./navigation";

const primarySections = ["Workspace"] as const;

export function AppShell() {
  const health = useQuery(healthQuery());
  const programs = usePrograms();
  const theme = useTheme();
  const location = useLocation();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [navigationOpen, setNavigationOpen] = useState(false);

  useEffect(() => {
    const commands = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
      if (event.key === "Escape") setPaletteOpen(false);
    };
    window.addEventListener("keydown", commands);
    return () => window.removeEventListener("keydown", commands);
  }, []);

  const current = navigationItems.find((item) => location.pathname.startsWith(item.path));

  return (
    <div className="vi-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      {navigationOpen ? <button className="navigation-scrim" type="button" aria-label="Close navigation" onClick={() => setNavigationOpen(false)} /> : null}
      <aside className={`vi-sidebar ${navigationOpen ? "is-open" : ""}`}>
        <NavLink className="vi-brand" to="/" onClick={() => setNavigationOpen(false)}>
          <span className="vi-brand-mark" aria-hidden="true"><Icon name="spark" /></span>
          <span><strong>Venture</strong><small>Intelligence workspace</small></span>
        </NavLink>
        <nav aria-label="Primary navigation">
          {primarySections.map((section) => (
            <div className="nav-section" key={section}>
              <p>{section}</p>
              {navigationItems.filter((item) => item.section === section).map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  end={item.path === "/"}
                  onClick={() => setNavigationOpen(false)}
                >
                  <Icon name={item.icon} />
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
          <details className="nav-advanced" open={current?.section === "System" || undefined}>
            <summary>System</summary>
            <div className="nav-section">
              {navigationItems.filter((item) => item.section === "System").map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  onClick={() => setNavigationOpen(false)}
                >
                  <Icon name={item.icon} />
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </div>
          </details>
        </nav>
        <div className="sidebar-footer">
          <div className="sidebar-status">
            <span className={`health-dot ${health.data?.status === "ok" ? "online" : "offline"}`} />
            <span>{health.isLoading ? "Connecting" : health.data?.status === "ok" ? "Systems operational" : "Backend unavailable"}</span>
          </div>
        </div>
      </aside>
      <div className="vi-workspace">
        <header className="vi-toolbar">
          <button
            type="button"
            className="mobile-menu"
            aria-label="Toggle navigation"
            aria-expanded={navigationOpen}
            onClick={() => setNavigationOpen((value) => !value)}
          >
            <Icon name="menu" />
          </button>
          <div className="toolbar-context"><span>Workspace</span><strong>{current?.label ?? "Intelligence"}</strong></div>
          <button type="button" className="command-trigger" onClick={() => setPaletteOpen(true)}>
            <Icon name="command" /><span>Search anything</span><kbd>Ctrl K</kbd>
          </button>
          <label className="toolbar-select">
            <span>Program scope</span>
            <select
              aria-label="Monitoring program"
              value={programs.selectedProgram}
              disabled={programs.isLoading}
              onChange={(event) => programs.setSelectedProgram(event.target.value)}
            >
              <option value="">All programs</option>
              {programs.programs.map((program) => (
                <option key={program.id} value={program.id}>{program.name} · {program.batch}</option>
              ))}
            </select>
          </label>
          <label className="toolbar-select theme-select" title="Theme preference">
            <span aria-hidden="true"><Icon name={theme.resolved === "dark" ? "moon" : "sun"} /></span>
            <select
              aria-label="Theme preference"
              value={theme.preference}
              onChange={(event) => theme.setPreference(event.target.value as ThemePreference)}
            >
              <option value="system">Auto</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
        </header>
        <main className={`vi-content ${location.pathname.startsWith("/research") ? "wide-content" : ""}`} id="main-content">
          <Outlet />
        </main>
      </div>
      {paletteOpen ? <CommandPalette onClose={() => setPaletteOpen(false)} /> : null}
    </div>
  );
}
