import { createContext, useContext } from "react";
import type { Bootstrap } from "../types";

export interface BootstrapContextValue { bootstrap: Bootstrap; refresh: () => void }
export const BootstrapContext = createContext<BootstrapContextValue | null>(null);
export function useBootstrap() {
  const value = useContext(BootstrapContext);
  if (!value) throw new Error("Bootstrap context is unavailable.");
  return value;
}
