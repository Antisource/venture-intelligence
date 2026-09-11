import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createHashRouter, RouterProvider } from "react-router-dom";

import { AppProviders } from "./app/AppProviders";
import { appRoutes } from "./app/router";
import "./styles.css";
import "./styles/tokens.css";
import "./styles/shell.css";
import "./styles/product.css";

const container = document.getElementById("app");
if (!container) throw new Error("Application mount point is missing");

const router = createHashRouter(appRoutes);
createRoot(container).render(
  <StrictMode>
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  </StrictMode>,
);
