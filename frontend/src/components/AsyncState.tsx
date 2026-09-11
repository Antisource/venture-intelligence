import type { ReactNode } from "react";

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : "An unexpected error occurred.";
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state-card state-loading" role="status" aria-live="polite">
      <span className="state-spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({
  title = "Nothing to show yet",
  detail,
}: {
  title?: string;
  detail?: string;
}) {
  return (
    <div className="state-card state-empty">
      <strong>{title}</strong>
      {detail ? <p>{detail}</p> : null}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  return (
    <div className="state-card state-error" role="alert">
      <strong>Unable to load this view</strong>
      <p>{messageFor(error)}</p>
    </div>
  );
}

export function PartialState({ children }: { children: ReactNode }) {
  return (
    <div className="state-card state-partial" role="status">
      <strong>Partial data</strong>
      <span>{children}</span>
    </div>
  );
}

export function AsyncState({
  loading,
  error,
  empty,
  children,
}: {
  loading: boolean;
  error: unknown;
  empty?: boolean;
  children: ReactNode;
}) {
  if (loading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  if (empty) return <EmptyState />;
  return children;
}
