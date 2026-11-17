// @vitest-environment jsdom
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient, setApiInstance } from "../api";
import type { WSClient } from "../api/ws-client";
import { chatKeys, chatSessionOptions, chatSessionsOptions } from "../chat/queries";
import { setCurrentWorkspace } from "../platform/workspace-storage";
import type { ChatSession } from "../types/chat";
import type { WSMessage } from "../types/events";
import { useRealtimeSync, type RealtimeSyncStores } from "./use-realtime-sync";

vi.mock("../paths", () => ({
  useHasOnboarded: () => true,
  resolvePostAuthDestination: () => "/",
}));

let qc: QueryClient;
const initial: ChatSession = {
  id: "chat-1", workspace_id: "ws-1", agent_id: "agent-1", creator_id: "user-1",
  title: "Chat", status: "active", has_unread: false,
  created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T00:00:00Z",
  execution_config: { model: null }, execution_config_supported: true,
  execution_config_capabilities: { model: true, thinking_level: true, service_tier: true, approval_policy: true },
  effective_execution_config: { model: "model-a", thinking_level: "high", service_tier: "priority", approval_policy: "" },
};

/** Observe the list and one detail while a second detail remains inactive. */
function Wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

afterEach(async () => {
  cleanup();
  qc?.clear();
  await act(async () => setCurrentWorkspace(null, null));
  vi.unstubAllGlobals();
});

// Removing either chat invalidation from the actual agent-prefix handler leaves
// inherited settings stuck at A despite the server now resolving the agent to B.
it("refetches inherited chat settings after the agent status event used by UpdateAgent", async () => {
  await act(async () => setCurrentWorkspace("workspace", "ws-1"));
  setApiInstance(new ApiClient("https://api.example.test"));
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const inactive = { ...initial, id: "chat-2" };
  const foreign = { ...initial, id: "foreign", workspace_id: "ws-2" };
  qc.setQueryData(chatKeys.sessions("ws-1"), [initial, inactive]);
  qc.setQueryData(chatKeys.session("ws-1", initial.id), initial);
  qc.setQueryData(chatKeys.session("ws-1", inactive.id), inactive);
  qc.setQueryData(chatKeys.sessions("ws-2"), [foreign]);
  qc.setQueryData(chatKeys.session("ws-2", foreign.id), foreign);
  const effective = { ...initial.effective_execution_config!, model: "model-b", thinking_level: "low", service_tier: "" };
  const reads: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: unknown) => {
    const path = new URL(String(url)).pathname;
    reads.push(path);
    const data = path === "/api/chat/sessions"
      ? [initial, inactive].map((row) => ({ ...row, effective_execution_config: effective }))
      : { ...initial, id: path.split("/").at(-1), effective_execution_config: effective };
    return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
  }));
  let receive: ((message: WSMessage) => void) | undefined;
  const ws = {
    on: vi.fn(() => () => {}),
    onAny: vi.fn((handler: (message: WSMessage) => void) => {
      receive = handler;
      return () => { receive = undefined; };
    }),
    onReconnect: vi.fn(() => () => {}),
    drop: vi.fn(),
  } as unknown as WSClient;
  const auth = { user: { id: "user-1" } };
  const stores = { authStore: Object.assign((selector: (state: typeof auth) => unknown) => selector(auth), {
    getState: () => auth, subscribe: () => () => {}, setState: () => {}, destroy: () => {},
  }) } as unknown as RealtimeSyncStores;
  const { result } = renderHook(() => {
    useRealtimeSync(ws, stores);
    return {
      list: useQuery(chatSessionsOptions("ws-1")).data,
      detail: useQuery(chatSessionOptions("ws-1", initial.id)).data,
    };
  }, { wrapper: Wrapper });
  expect(reads).toEqual([]);
  act(() => receive!({ type: "agent:status", payload: { agent: { id: "agent-1", model: "model-b", runtime_id: "runtime-b" } } }));
  await waitFor(() => {
    expect(result.current.list?.[0]?.effective_execution_config).toEqual(effective);
    expect(result.current.detail?.effective_execution_config).toEqual(effective);
  });
  expect(reads).toEqual(expect.arrayContaining(["/api/chat/sessions", "/api/chat/sessions/chat-1"]));
  expect(result.current.detail?.execution_config).toEqual({ model: null });
  expect(qc.getQueryState(chatKeys.session("ws-1", inactive.id))?.isInvalidated).toBe(true);
  await act(async () => { await qc.fetchQuery(chatSessionOptions("ws-1", inactive.id)); });
  expect(qc.getQueryData<ChatSession>(chatKeys.session("ws-1", inactive.id))?.effective_execution_config).toEqual(effective);
  expect(qc.getQueryState(chatKeys.sessions("ws-2"))?.isInvalidated).toBe(false);
  expect(qc.getQueryState(chatKeys.session("ws-2", foreign.id))?.isInvalidated).toBe(false);
  expect(qc.getQueryData<ChatSession[]>(chatKeys.sessions("ws-2"))?.[0]).toEqual(foreign);
});
