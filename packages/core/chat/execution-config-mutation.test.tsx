// @vitest-environment jsdom
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient, setApiInstance } from "../api";
import { setCurrentWorkspace } from "../platform/workspace-storage";
import type { ChatSession } from "../types/chat";
import { useSetChatSessionExecutionConfig } from "./mutations";
import { chatKeys, chatSessionOptions, chatSessionsOptions } from "./queries";

vi.mock("../hooks", async (original) => ({
  ...(await original<typeof import("../hooks")>()),
  useWorkspaceId: () => "ws-1",
}));

let qc: QueryClient;
const initial: ChatSession = {
  id: "chat-1", workspace_id: "ws-1", agent_id: "agent-1", creator_id: "user-1",
  title: "Chat", status: "active", has_unread: false,
  created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T00:00:00Z",
  execution_config: { model: "initial" }, execution_config_supported: true,
  execution_config_capabilities: { model: true, thinking_level: true, service_tier: true, approval_policy: true },
  effective_execution_config: { model: "initial", thinking_level: "", service_tier: "", approval_policy: "" },
};

/** Keep both server projections observed as they are across open chat surfaces. */
function Wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

afterEach(async () => {
  cleanup();
  qc?.clear();
  await act(async () => setCurrentWorkspace(null, null));
  vi.unstubAllGlobals();
});

// Restoring the HTTP-response cache write rolls B back to A while GETs wait;
// removing either settle invalidation strands its Infinity-cached projection.
it("keeps a newer refetched config while a late save settles, then refetches both projections", async () => {
  await act(async () => setCurrentWorkspace("workspace", "ws-1"));
  setApiInstance(new ApiClient("https://api.example.test"));
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  qc.setQueryData(chatKeys.sessions("ws-1"), [initial]);
  qc.setQueryData(chatKeys.session("ws-1", initial.id), initial);
  let serverSession = initial;
  let releasePatch!: () => void;
  let releaseReads!: () => void;
  const patchGate = new Promise<void>((resolve) => { releasePatch = resolve; });
  const readGate = new Promise<void>((resolve) => { releaseReads = resolve; });
  const reads: string[] = [];
  let writes = 0;
  let holdReads = false;
  vi.stubGlobal("fetch", vi.fn(async (url: unknown, init?: RequestInit) => {
    const path = new URL(String(url)).pathname;
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body)) as { execution_config: ChatSession["execution_config"] };
      serverSession = { ...initial, execution_config: body.execution_config,
        effective_execution_config: { ...initial.effective_execution_config!, model: "model-a" },
        updated_at: "2026-11-14T00:01:00Z" };
      const acknowledged = serverSession;
      writes++;
      await patchGate;
      return new Response(JSON.stringify(acknowledged), { headers: { "Content-Type": "application/json" } });
    }
    reads.push(path);
    if (holdReads) await readGate;
    return new Response(JSON.stringify(path === "/api/chat/sessions" ? [serverSession] : serverSession),
      { headers: { "Content-Type": "application/json" } });
  }));
  const { result } = renderHook(() => ({
    list: useQuery(chatSessionsOptions("ws-1")).data,
    detail: useQuery(chatSessionOptions("ws-1", initial.id)).data,
    save: useSetChatSessionExecutionConfig(),
  }), { wrapper: Wrapper });
  let saving!: Promise<ChatSession>;
  act(() => { saving = result.current.save.mutateAsync({ sessionId: initial.id, config: { model: "model-a" } }); });
  await waitFor(() => expect(writes).toBe(1));
  serverSession = { ...serverSession, execution_config: { model: "model-b" },
    effective_execution_config: { ...serverSession.effective_execution_config!, model: "model-b" },
    updated_at: "2026-11-14T00:02:00Z" };
  await act(async () => {
    await Promise.all([
      qc.refetchQueries({ queryKey: chatKeys.sessions("ws-1") }),
      qc.refetchQueries({ queryKey: chatKeys.session("ws-1", initial.id) }),
    ]);
  });
  await waitFor(() => {
    expect(result.current.list?.[0]?.execution_config?.model).toBe("model-b");
    expect(result.current.detail?.execution_config?.model).toBe("model-b");
  });
  holdReads = true;
  act(() => releasePatch());
  try {
    await waitFor(() => expect(reads).toHaveLength(4));
    expect(reads.filter((path) => path === "/api/chat/sessions")).toHaveLength(2);
    expect(reads.filter((path) => path === "/api/chat/sessions/chat-1")).toHaveLength(2);
    expect(qc.getQueryData<ChatSession[]>(chatKeys.sessions("ws-1"))?.[0]).toMatchObject(serverSession);
    expect(qc.getQueryData<ChatSession>(chatKeys.session("ws-1", initial.id))).toMatchObject(serverSession);
    expect(result.current.save.isPending).toBe(true);
  } finally {
    await act(async () => { releaseReads(); await saving; });
  }
  await waitFor(() => expect(result.current.save.isPending).toBe(false));
  expect(result.current.list?.[0]).toMatchObject(serverSession);
  expect(result.current.detail).toMatchObject(serverSession);
});
