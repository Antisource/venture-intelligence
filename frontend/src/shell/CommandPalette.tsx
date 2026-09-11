import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { searchQuery } from "../api/queries";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { Icon } from "../components/Icon";
import { navigationItems } from "./navigation";

function safeRoute(route: string): string {
  return route.startsWith("/") && !route.startsWith("//") ? route : "/";
}

export function CommandPalette({ onClose }: { onClose: () => void }) {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const navigate = useNavigate();

  useEffect(() => {
    const timeout = window.setTimeout(() => setQuery(input.trim()), 180);
    return () => window.clearTimeout(timeout);
  }, [input]);

  const search = useQuery(searchQuery(query));
  const localResults = useMemo(() => {
    const term = input.trim().toLocaleLowerCase();
    if (!term) return navigationItems.slice(0, 5);
    return navigationItems.filter((item) => item.label.toLocaleLowerCase().includes(term));
  }, [input]);

  const go = (route: string) => {
    void navigate(safeRoute(route));
    onClose();
  };
  const destinations = [
    ...localResults.map((item) => item.path),
    ...(search.data ?? []).map((item) => item.route),
  ];

  return (
    <div className="command-backdrop" role="presentation" onMouseDown={onClose}>
      <div
        className="command-palette"
        role="dialog"
        aria-modal="true"
        aria-label="Global command search"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <label className="command-input-row">
          <Icon name="command" />
          <input
            autoFocus
            value={input}
            onChange={(event) => { setInput(event.target.value); setSelectedIndex(0); }}
            onKeyDown={(event) => {
              if (event.key === "Escape") onClose();
              if (event.key === "ArrowDown") { event.preventDefault(); setSelectedIndex((index) => Math.min(index + 1, destinations.length - 1)); }
              if (event.key === "ArrowUp") { event.preventDefault(); setSelectedIndex((index) => Math.max(index - 1, 0)); }
              if (event.key === "Enter" && destinations[selectedIndex]) go(destinations[selectedIndex]);
            }}
            placeholder="Search founders, companies, programs, or commands"
            aria-label="Search commands and validated entities"
          />
          <kbd>Esc</kbd>
        </label>
        <div className="command-results">
          {localResults.length ? (
            <section aria-label="Navigation commands">
              <p className="command-section-label">Navigate</p>
              {localResults.map((item, index) => (
                <button className={selectedIndex === index ? "selected" : ""} type="button" key={item.path} onMouseEnter={() => setSelectedIndex(index)} onClick={() => go(item.path)}>
                  <span className="command-result-main"><Icon name={item.icon} />{item.label}</span><small>{item.section}</small>
                </button>
              ))}
            </section>
          ) : null}
          {input.trim().length >= 2 ? (
            <section aria-label="Validated entity results">
              <p className="command-section-label">Validated intelligence</p>
              {search.isLoading ? <LoadingState label="Searching…" /> : null}
              {search.error ? <ErrorState error={search.error} /> : null}
              {!search.isLoading && !search.error && search.data?.length === 0 ? (
                <p className="command-empty">No validated matches.</p>
              ) : null}
              {search.data?.map((result, index) => {
                const destinationIndex = localResults.length + index;
                return <button className={selectedIndex === destinationIndex ? "selected" : ""} type="button" key={`${result.kind}:${result.id}`} onMouseEnter={() => setSelectedIndex(destinationIndex)} onClick={() => go(result.route)}>
                  <span className="command-result-main"><Icon name={result.kind === "person" ? "founder" : "building"} />{result.label}</span>
                  <small>{result.kind}{result.context ? ` · ${result.context}` : ""}</small>
                </button>;
              })}
            </section>
          ) : null}
        </div>
      </div>
    </div>
  );
}
