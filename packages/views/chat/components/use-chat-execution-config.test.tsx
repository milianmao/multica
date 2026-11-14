// @vitest-environment jsdom
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@multica/core/i18n/react";
import { ApiClient, setApiInstance } from "@multica/core/api";
import { createChatStore, registerChatStore } from "@multica/core/chat";
import { chatKeys, chatSessionOptions } from "@multica/core/chat/queries";
import { agentListOptions, memberListOptions } from "@multica/core/workspace/queries";
import { projectListOptions } from "@multica/core/projects/queries";
import { agentTaskSnapshotOptions } from "@multica/core/agents/queries";
import { runtimeListOptions, runtimeModelsKeys } from "@multica/core/runtimes";
import { setCurrentWorkspace } from "@multica/core/platform";
import type { Agent, AgentRuntime, ChatSession } from "@multica/core/types";
import type { ChatExecutionConfig } from "@multica/core/types/chat";
import enChat from "../../locales/en/chat.json";
import { useChatController } from "./use-chat-controller";
import { useChatExecutionConfig } from "./use-chat-execution-config";

vi.mock("@multica/core/hooks", async (original) => ({
  ...(await original<typeof import("@multica/core/hooks")>()), useWorkspaceId: () => "ws-1",
}));
vi.mock("@multica/core/auth", async (original) => {
  const state = { user: { id: "user-1" } };
  return { ...(await original<typeof import("@multica/core/auth")>()),
    useAuthStore: Object.assign((selector: (value: typeof state) => unknown) => selector(state), { getState: () => state }) };
});

const agent = { id: "agent-1", workspace_id: "ws-1", runtime_id: "runtime-1", name: "Codex", model: "model-a", thinking_level: "high", service_tier: "priority", owner_id: "user-1", permission_mode: "private", archived_at: null } as Agent;
const runtime = { id: "runtime-1", workspace_id: "ws-1", provider: "codex", status: "online", owner_id: "user-1", metadata: {} } as AgentRuntime;
const base: ChatSession = {
  id: "chat-1", workspace_id: "ws-1", agent_id: agent.id, creator_id: "user-1", title: "Chat", status: "active", has_unread: true, unread_count: 3,
  created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T00:00:00Z",
  execution_config_supported: true, execution_config_capabilities: { model: true, thinking_level: true, service_tier: true, approval_policy: true },
  execution_config: { model: "model-a", approval_policy: null },
  effective_execution_config: { model: "model-a", thinking_level: "high", service_tier: "priority", approval_policy: "" },
};
let qc: QueryClient;
let store: ReturnType<typeof createChatStore>;
let session: ChatSession;
let requests: Array<{ method: string; path: string; body: Record<string, unknown> }>;
let rejectSave: boolean;
let releaseSave: (() => void) | undefined;
let holdSave: boolean;

function wrapper({ children }: { children: ReactNode }) {
  return <I18nProvider locale="en" resources={{ en: { chat: enChat } }}><QueryClientProvider client={qc}>{children}</QueryClientProvider></I18nProvider>;
}
function composer() {
  return renderHook(() => { const controller = useChatController({ isActive: false }); return { controller, controls: useChatExecutionConfig(controller) }; }, { wrapper });
}

beforeEach(async () => {
  await act(async () => setCurrentWorkspace("workspace", "ws-1"));
  const values = new Map<string, string>();
  store = createChatStore({ storage: { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); }, removeItem: (key) => { values.delete(key); } } });
  registerChatStore(store);
  store.setState({ activeSessionId: base.id });
  setApiInstance(new ApiClient("https://api.example.test"));
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  session = { ...base, execution_config: { ...base.execution_config } };
  requests = []; rejectSave = false; holdSave = false; releaseSave = undefined;
  qc.setQueryData(agentListOptions("ws-1").queryKey, [agent]);
  qc.setQueryData(memberListOptions("ws-1").queryKey, [{ id: "member-1", workspace_id: "ws-1", user_id: "user-1", role: "owner", created_at: base.created_at, name: "User", email: "user@example.test", avatar_url: null }]);
  qc.setQueryData(projectListOptions("ws-1").queryKey, { projects: [], total: 0 });
  qc.setQueryData(runtimeListOptions("ws-1").queryKey, [runtime]);
  qc.setQueryData(agentTaskSnapshotOptions("ws-1").queryKey, []);
  qc.setQueryData(chatKeys.sessions("ws-1"), [session]);
  qc.setQueryData(chatKeys.session("ws-1", session.id), session);
  qc.setQueryData(chatKeys.messagesPage(session.id), { pages: [{ messages: [], has_more: false }], pageParams: [undefined] });
  qc.setQueryData(chatKeys.pendingTask(session.id), null);
  qc.setQueryData(runtimeModelsKeys.forRuntime(runtime.id), { supported: true, cached: false, unavailableModels: [], models: [
    { id: "model-a", label: "A", thinking: { supported_levels: [{ value: "high", label: "High" }] }, service_tiers: [{ id: "priority", name: "Fast" }] },
    { id: "model-b", label: "B", thinking: { supported_levels: [{ value: "low", label: "Low" }] } },
  ] });
  vi.stubGlobal("fetch", vi.fn(async (url: unknown, init?: RequestInit) => {
    const path = new URL(String(url)).pathname;
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
    requests.push({ method, path, body });
    let data: unknown;
    if (method === "PATCH") {
      if (holdSave) await new Promise<void>((resolve) => { releaseSave = resolve; });
      if (rejectSave) return new Response(JSON.stringify({ error: "cannot save config" }), { status: 500 });
      session = { ...session, execution_config: { ...session.execution_config, ...body.execution_config as ChatExecutionConfig } };
      data = session;
    } else if (path.endsWith("/models")) {
      data = { id: "catalog-1", runtime_id: runtime.id, status: "failed", error: "runtime disconnected", models: [] };
    } else if (path === "/api/chat/sessions") data = [session];
    else if (path === "/api/chat/sessions/" + session.id) data = session;
    else if (path.endsWith("/pending-task")) data = null;
    else data = { messages: [], restores: [] };
    return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(async () => { cleanup(); qc.clear(); vi.unstubAllGlobals(); await act(async () => setCurrentWorkspace(null, null)); });

describe("shared chat execution settings", () => {
  // Removing the awaited Query updates either shows an optimistic model on a
  // failed save or leaves the other surface on its stale Infinity-cached row.
  it("saves atomic model cleanup to both Queries only after success and preserves values on failure", async () => {
    const { result } = composer();
    holdSave = true;
    let saving: Promise<void>;
    await act(async () => { saving = result.current.controls.changeModel("model-b"); });
    await waitFor(() => expect(releaseSave).toBeTypeOf("function"));
    expect(result.current.controls.config.model).toBe("model-a");
    await act(async () => { releaseSave?.(); await saving!; });
    expect(requests.find((request) => request.method === "PATCH")?.body).toEqual({ execution_config: { model: "model-b", thinking_level: "", service_tier: "" } });
    await waitFor(() => expect(result.current.controls.config.model).toBe("model-b"));
    expect(qc.getQueryState(chatKeys.session("ws-1", session.id))?.isInvalidated).toBe(true);
    const refreshed = await qc.fetchQuery(chatSessionOptions("ws-1", session.id));
    expect(refreshed.execution_config).toEqual(session.execution_config);
    expect(qc.getQueryData<ChatSession[]>(chatKeys.sessions("ws-1"))?.[0]?.unread_count).toBe(3);
    expect(store.getState().draftExecutionConfigs).toEqual({});
    holdSave = false; rejectSave = true;
    await act(async () => { await result.current.controls.changeApproval("full-access"); });
    await waitFor(() => expect(result.current.controls.error).not.toBeNull());
    expect(result.current.controls.config).toEqual({ model: "model-b", thinking_level: "", service_tier: "", approval_policy: null });
  });

  // Removing the draft branch creates a session while choosing options; losing
  // the null merge overwrites an explicit inherited policy with the new default.
  it("keeps unsent settings in the draft and lets explicit null override the new Codex default", async () => {
    store.getState().setActiveSession(null);
    const { result } = composer();
    expect(result.current.controls.config.approval_policy).toBe("on-request");
    await act(async () => { await result.current.controls.changeModel(null); await result.current.controls.changeApproval(null); });
    expect(store.getState().draftExecutionConfigs[agent.id]).toEqual({ model: null, thinking_level: null, service_tier: null, approval_policy: null });
    expect(result.current.controller.activeSessionId).toBeNull();
    expect(requests.filter((request) => request.method !== "GET")).toEqual([]);
  });

  // Removing the success-state gate advertises old cached rows after a failed
  // live discovery; removing the support gate enables a legacy server's policy.
  it("disables model and effort after catalog failure and disables all overrides without explicit support", async () => {
    const { result } = composer();
    expect(result.current.controls.modelDisabled).toBe(false);
    act(() => result.current.controls.refreshCatalog());
    await waitFor(() => expect(result.current.controls.catalogUnavailable).toBe(true));
    expect(result.current.controls.modelDisabled).toBe(true);
    expect(result.current.controls.thinkingDisabled).toBe(true);
    expect(result.current.controls.approvalDisabled).toBe(false);
    await act(async () => { await result.current.controls.changeModel("model-b"); });
    expect(requests.filter((request) => request.method === "PATCH")).toEqual([]);
    act(() => qc.setQueryData(chatKeys.sessions("ws-1"), [{ ...session, execution_config_supported: false }]));
    await waitFor(() => expect(result.current.controls.approvalDisabled).toBe(true));
    expect(result.current.controls.canDiscoverModels).toBe(false);
  });
});
