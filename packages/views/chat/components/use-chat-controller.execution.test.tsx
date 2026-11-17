// @vitest-environment jsdom
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@multica/core/i18n/react";
import { ApiClient, setApiInstance } from "@multica/core/api";
import { createChatStore, registerChatStore } from "@multica/core/chat";
import { chatKeys, chatSessionsOptions } from "@multica/core/chat/queries";
import { agentListOptions, memberListOptions } from "@multica/core/workspace/queries";
import { projectListOptions } from "@multica/core/projects/queries";
import { agentTaskSnapshotOptions } from "@multica/core/agents/queries";
import { runtimeListOptions } from "@multica/core/runtimes";
import { setCurrentWorkspace } from "@multica/core/platform";
import type { Agent, AgentRuntime, ChatSession } from "@multica/core/types";
import type { ChatExecutionConfig } from "@multica/core/types/chat";
import enChat from "../../locales/en/chat.json";
import { useChatController } from "./use-chat-controller";

vi.mock("@multica/core/hooks", async (original) => ({
  ...(await original<typeof import("@multica/core/hooks")>()),
  useWorkspaceId: () => "ws-1",
}));
vi.mock("@multica/core/auth", async (original) => {
  const state = { user: { id: "user-1" } };
  return {
    ...(await original<typeof import("@multica/core/auth")>()),
    useAuthStore: Object.assign((selector: (value: typeof state) => unknown) => selector(state), { getState: () => state }),
  };
});

const agent = { id: "agent-1", workspace_id: "ws-1", runtime_id: "runtime-1", name: "Codex", model: "model-a", owner_id: "user-1", permission_mode: "private", archived_at: null } as Agent;
const runtime = { id: "runtime-1", workspace_id: "ws-1", provider: "codex", status: "online", owner_id: "user-1", metadata: {} } as AgentRuntime;
const support = { execution_config_supported: true, execution_config_capabilities: { model: true, thinking_level: true, service_tier: true, approval_policy: true } };
const base: ChatSession = { id: "chat-1", workspace_id: "ws-1", agent_id: agent.id, creator_id: "user-1", title: "Chat", status: "active", has_unread: false, created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T00:00:00Z" };
const taskId = "6af44cbe-80ab-4dfe-b07d-bd3cfd588f4d";
let qc: QueryClient;
let store: ReturnType<typeof createChatStore>;
let sessions: ChatSession[];
let requests: Array<{ method: string; path: string; body: Record<string, unknown> }>;
let supported: boolean;
let releaseCreate: (() => void) | undefined;
let holdCreate: boolean;
let runtimeRows: AgentRuntime[];

function wrapper({ children }: { children: ReactNode }) {
  return <I18nProvider locale="en" resources={{ en: { chat: enChat } }}><QueryClientProvider client={qc}>{children}</QueryClientProvider></I18nProvider>;
}

beforeEach(async () => {
  await act(async () => setCurrentWorkspace("workspace", "ws-1"));
  const values = new Map<string, string>();
  store = createChatStore({ storage: { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); }, removeItem: (key) => { values.delete(key); } } });
  registerChatStore(store);
  setApiInstance(new ApiClient("https://api.example.test"));
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  sessions = [];
  requests = [];
  supported = true;
  holdCreate = false;
  releaseCreate = undefined;
  runtimeRows = [runtime];
  qc.setQueryData(agentListOptions("ws-1").queryKey, [agent]);
  qc.setQueryData(memberListOptions("ws-1").queryKey, [{ id: "member-1", workspace_id: "ws-1", user_id: "user-1", role: "owner", created_at: base.created_at, name: "User", email: "user@example.test", avatar_url: null }]);
  qc.setQueryData(projectListOptions("ws-1").queryKey, { projects: [], total: 0 });
  qc.setQueryData(runtimeListOptions("ws-1").queryKey, [runtime]);
  qc.setQueryData(agentTaskSnapshotOptions("ws-1").queryKey, []);
  qc.setQueryData(chatSessionsOptions("ws-1").queryKey, []);
  vi.stubGlobal("fetch", vi.fn(async (url: unknown, init?: RequestInit) => {
    const path = new URL(String(url), "https://api.example.test").pathname;
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
    requests.push({ method, path, body });
    let data: unknown;
    if (method === "POST" && path === "/api/chat/sessions") {
      const config = body.execution_config as ChatExecutionConfig | undefined;
      const session = { ...base, id: "chat-" + (sessions.length + 1), execution_config: config ?? {}, ...(supported ? support : {}) };
      sessions = [...sessions, session];
      if (holdCreate) await new Promise<void>((resolve) => { releaseCreate = resolve; });
      data = session;
    } else if (method === "PATCH") {
      const id = path.split("/").at(-1);
      sessions = sessions.map((session) => session.id === id ? { ...session, execution_config: { ...session.execution_config, ...body.execution_config as ChatExecutionConfig } } : session);
      data = sessions.find((session) => session.id === id);
    } else if (method === "POST" && path.endsWith("/messages")) {
      data = { message_id: "message-" + requests.length, task_id: taskId, created_at: base.created_at, supports_queue: true };
    } else if (path === "/api/runtimes") {
      data = runtimeRows;
    } else if (path === "/api/chat/sessions") {
      data = sessions;
    } else if (path.endsWith("/pending-task")) {
      data = null;
    } else {
      data = { messages: [], restores: [] };
    }
    return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(async () => {
  cleanup();
  qc.clear();
  await act(async () => setCurrentWorkspace(null, null));
  vi.unstubAllGlobals();
});

describe("chat execution send wiring", () => {
  // Removing the create snapshot or accepted-draft identity guard makes later
  // edits leak into this send or disappear when its first session is published.
  it("creates from the captured draft and retains settings edited during the send", async () => {
    store.getState().setDraftExecutionConfig(agent.id, { model: "model-a", thinking_level: "high", approval_policy: null });
    holdCreate = true;
    const view = renderHook(() => useChatController({ isActive: false }), { wrapper });
    let send!: Promise<boolean>;
    act(() => { send = view.result.current.handleSend("hello"); });
    await waitFor(() => expect(releaseCreate).toBeTypeOf("function"));
    act(() => store.getState().setDraftExecutionConfig(agent.id, { model: "model-b", approval_policy: "full-access" }));
    await act(async () => { releaseCreate!(); expect(await send).toBe(true); });
    expect(requests.find((request) => request.method === "POST" && request.path === "/api/chat/sessions")?.body.execution_config)
      .toEqual({ model: "model-a", thinking_level: "high", approval_policy: null });
    expect(requests.filter((request) => request.method === "PATCH")).toEqual([]);
    expect(store.getState().draftExecutionConfigs[agent.id]).toEqual({ model: "model-b", approval_policy: "full-access" });
    const sessionId = store.getState().activeSessionId!;
    expect(qc.getQueryData<ChatSession>(chatKeys.session("ws-1", sessionId))?.execution_config)
      .toEqual({ model: "model-a", thinking_level: "high", approval_policy: null });
  });

  // The backend may only reveal support after lazy creation. Defaulting earlier
  // falsely promises approval on old servers; defaulting all sends changes old chats.
  it("applies on-request only to a new chat after backend confirmation, not to old chats", async () => {
    supported = false;
    sessions = [{ ...base, execution_config: {} }];
    qc.setQueryData(chatSessionsOptions("ws-1").queryKey, sessions);
    store.getState().setActiveSession(base.id);
    const view = renderHook(() => useChatController({ isActive: false }), { wrapper });
    await act(async () => { expect(await view.result.current.handleSend("old chat")).toBe(true); });
    expect(requests.some((request) => request.method === "PATCH")).toBe(false);
    act(() => view.result.current.handleNewChat());
    await act(async () => { expect(await view.result.current.handleSend("old backend")).toBe(true); });
    expect(requests.some((request) => request.method === "PATCH")).toBe(false);
    supported = true;
    act(() => view.result.current.handleNewChat());
    await act(async () => { expect(await view.result.current.handleSend("new backend")).toBe(true); });
    const writes = requests.filter((request) => request.method !== "GET");
    const patchIndex = writes.findIndex((request) => request.method === "PATCH");
    expect(patchIndex).toBeGreaterThanOrEqual(0);
    expect(writes[patchIndex]?.body).toEqual({ execution_config: { approval_policy: "on-request" } });
    expect(writes[patchIndex + 1]?.path.endsWith("/messages")).toBe(true);
    expect(sessions.find((session) => session.id === base.id)?.execution_config).toEqual({});
  });
  // Requiring runtime identity after the server confirms approval support
  // blocks a callable shared agent whose private runtime is not visible.
  it("sends a new shared-agent chat with on-request even when its runtime is not visible", async () => {
    const sharedAgent: Agent = { ...agent, owner_id: "other-user", permission_mode: "public_to",
      invocation_targets: [{ target_type: "workspace", target_id: null }] };
    qc.setQueryData(agentListOptions("ws-1").queryKey, [sharedAgent]);
    runtimeRows = [];
    qc.removeQueries({ queryKey: runtimeListOptions("ws-1").queryKey });
    const { result } = renderHook(() => useChatController({ isActive: false }), { wrapper });
    await waitFor(() => expect(requests.some((request) => request.path === "/api/runtimes")).toBe(true));
    expect(result.current.runtime).toBeNull();
    expect(result.current.isAgentAccessRevoked).toBe(false);
    await act(async () => { expect(await result.current.handleSend("first message")).toBe(true); });
    const writes = requests.filter((request) => request.method !== "GET");
    expect(writes.map((request) => request.method)).toEqual(["POST", "PATCH", "POST"]);
    expect(writes[1]?.body).toEqual({ execution_config: { approval_policy: "on-request" } });
    expect(writes[2]?.path).toBe("/api/chat/sessions/chat-1/messages");
    expect(sessions).toHaveLength(1);
  });

});
