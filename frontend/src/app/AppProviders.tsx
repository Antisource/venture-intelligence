import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ProgramProvider } from "../state/ProgramProvider";
import { ThemeProvider } from "../theme/ThemeProvider";

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: 1,
        refetchOnWindowFocus: false,
      },
    },
  });
}

export function AppProviders({
  children,
  client = createQueryClient(),
}: {
  children: ReactNode;
  client?: QueryClient;
}) {
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <ProgramProvider>{children}</ProgramProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
