import { Navigate, useParams, useSearchParams, type RouteObject } from "react-router-dom";
import { SourcePolicyPage } from "../features/foundation/FoundationPage";
import { EvaluationPage } from "../features/evaluation/EvaluationPage";
import { RunsPage, SettingsPage } from "../features/operations/OperationsPages";
import { ResearchWorkspace } from "../features/research/ResearchWorkspace";
import { InboxPage, DiscoverPage, PortfolioPage, researchLink } from "../features/ProductWorkspaces";
import { ReplayContractPage } from "../features/replay/ReplayContractPage";
import { AppShell } from "../shell/AppShell";

function EntityRedirect({ decisions = false }: { decisions?: boolean }) {
  const { entityId = "" } = useParams();
  return <Navigate replace to={researchLink(entityId) + (decisions ? "?view=decisions" : "")} />;
}
function GraphRedirect() {
  const [params] = useSearchParams();
  const focus = params.get("focus");
  return <Navigate replace to={(focus ? researchLink(focus) : "/research") + "?view=network"} />;
}
export const appRoutes: RouteObject[] = [{
  path: "/", element: <AppShell />, children: [
    { index: true, element: <Navigate replace to="/inbox" /> },
    { path: "inbox", element: <InboxPage /> },
    { path: "discover", element: <DiscoverPage /> },
    { path: "portfolio", element: <PortfolioPage /> },
    { path: "research", element: <ResearchWorkspace /> },
    { path: "research/:entityId", element: <ResearchWorkspace /> },
    { path: "system/sources", element: <SourcePolicyPage /> },
    { path: "system/runs", element: <RunsPage /> },
    { path: "system/settings", element: <SettingsPage /> },
    { path: "system/evaluate", element: <EvaluationPage /> },
    { path: "system/replay", element: <ReplayContractPage /> },
    ...["overview", "dashboard", "discovery", "changes", "alerts"].map((path) => ({ path, element: <Navigate replace to="/inbox" /> })),
    ...["companies", "founders"].map((path) => ({ path, element: <Navigate replace to={`/discover?type=${path}`} /> })),
    { path: "rankings", element: <Navigate replace to="/discover" /> },
    { path: "crm", element: <Navigate replace to="/portfolio" /> },
    ...["companies/:entityId", "founders/:entityId"].map((path) => ({ path, element: <EntityRedirect /> })),
    { path: "crm/:entityId", element: <EntityRedirect decisions /> },
    { path: "graph", element: <GraphRedirect /> },
    ...["chat", "analyst"].map((path) => ({ path, element: <Navigate replace to="/research?view=assistant" /> })),
    ...["sources", "runs", "settings", "replay", "evaluate"].map((path) => ({ path, element: <Navigate replace to={`/system/${path}`} /> })),
    { path: "*", element: <section><h1>Page not found</h1><a href="/">Return to Inbox</a></section> },
  ],
}];
