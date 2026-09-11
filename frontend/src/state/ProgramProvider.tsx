import { useQuery } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useContext,
  useMemo,
  useState,
} from "react";

import type { Program } from "../api/client.ts";
import { programsQuery } from "../api/queries";

interface ProgramContextValue {
  programs: Program[];
  selectedProgram: string;
  setSelectedProgram: (programId: string) => void;
  isLoading: boolean;
  error: Error | null;
}

const STORAGE_KEY = "venture:selected-program";
const ProgramContext = createContext<ProgramContextValue | null>(null);

export function ProgramProvider({ children }: { children: ReactNode }) {
  const query = useQuery(programsQuery());
  const [selectedProgram, setSelected] = useState(
    () => window.localStorage.getItem(STORAGE_KEY) ?? "",
  );
  const setSelectedProgram = (programId: string) => {
    setSelected(programId);
    window.localStorage.setItem(STORAGE_KEY, programId);
  };
  const effectiveProgram =
    selectedProgram
    && query.data
    && !query.data.programs.some((program) => program.id === selectedProgram)
      ? ""
      : selectedProgram;
  const value = useMemo(
    () => ({
      programs: query.data?.programs ?? [],
      selectedProgram: effectiveProgram,
      setSelectedProgram,
      isLoading: query.isLoading,
      error: query.error,
    }),
    [effectiveProgram, query.data, query.error, query.isLoading],
  );
  return <ProgramContext.Provider value={value}>{children}</ProgramContext.Provider>;
}

export function usePrograms(): ProgramContextValue {
  const value = useContext(ProgramContext);
  if (!value) throw new Error("usePrograms must be used inside ProgramProvider");
  return value;
}
