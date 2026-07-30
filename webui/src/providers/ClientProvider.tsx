import { createContext, useContext, type ReactNode } from "react";

import type { NanobotClient } from "@/lib/nanobot-client";
import type { WebUIIngressLimits } from "@/lib/types";

interface ClientContextValue {
  client: NanobotClient;
  token: string;
  modelName: string | null;
  ingressLimits: WebUIIngressLimits | null;
  onReauth?: () => Promise<string | null>;
}

const ClientContext = createContext<ClientContextValue | null>(null);

export function ClientProvider({
  client,
  token,
  modelName = null,
  ingressLimits = null,
  onReauth,
  children,
}: {
  client: NanobotClient;
  token: string;
  modelName?: string | null;
  ingressLimits?: WebUIIngressLimits | null;
  onReauth?: () => Promise<string | null>;
  children: ReactNode;
}) {
  return (
    <ClientContext.Provider value={{ client, token, modelName, ingressLimits, onReauth }}>
      {children}
    </ClientContext.Provider>
  );
}

export function useClient(): ClientContextValue {
  const ctx = useContext(ClientContext);
  if (!ctx) {
    throw new Error("useClient must be used within a ClientProvider");
  }
  return ctx;
}
