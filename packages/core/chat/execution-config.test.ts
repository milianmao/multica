// @vitest-environment node
import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../api/client";
import { ChatSessionSchema } from "../api/schemas";
import { setCurrentWorkspace } from "../platform/workspace-storage";
import { applyChatSessionUpdatedToCache } from "../realtime/use-realtime-sync";
import type { ChatSession } from "../types/chat";
import { chatKeys, getNewChatExecutionConfig } from "./queries";
import { createChatStore } from "./store";

const session: ChatSession = {
  id: "chat-1", workspace_id: "ws-1", agent_id: "agent-1", creator_id: "user-1",
  title: "Chat", status: "active", has_unread: true, unread_count: 2,
  created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T00:00:00Z",
  execution_config: { model: "model-a", thinking_level: "high", approval_policy: "on-request" },
  effective_execution_config: { model: "model-a", thinking_level: "high", service_tier: "", approval_policy: "on-request" },
  execution_config_supported: true,
  execution_config_capabilities: { model: true, thinking_level: true, service_tier: true, approval_policy: true },
};

afterEach(async () => {
  setCurrentWorkspace(null, null);
  await Promise.resolve();
  vi.unstubAllGlobals();
});

describe("chat execution config boundaries", () => {
  it("only defaults new Codex drafts to on-request after an explicit capability signal", () => {
    expect(getNewChatExecutionConfig(session)).toEqual({ approval_policy: "on-request" });
    expect(getNewChatExecutionConfig({ execution_config_supported: false })).toEqual({});
    expect(getNewChatExecutionConfig({ ...session,
      execution_config_capabilities: { ...session.execution_config_capabilities!, approval_policy: false },
    })).toEqual({});
    expect(getNewChatExecutionConfig()).toEqual({});
  });
  // Removing the request body or response parser loses explicit inheritance or
  // lets malformed config values become authoritative in both chat surfaces.
  it("sends create and PATCH overrides including null and parses returned sessions", async () => {
    const requests: Array<{ method: string | undefined; body: unknown }> = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: unknown, init?: RequestInit) => {
      requests.push({ method: init?.method, body: JSON.parse(String(init?.body)) });
      return new Response(JSON.stringify(session), { headers: { "Content-Type": "application/json" } });
    }));
    const api = new ApiClient("https://api.example.test");
    const created = await api.createChatSession({ agent_id: "agent-1", execution_config: { approval_policy: "on-request" } });
    const updated = await api.updateChatSession("chat-1", { execution_config: { model: null } });
    expect(requests).toEqual([
      { method: "POST", body: { agent_id: "agent-1", execution_config: { approval_policy: "on-request" } } },
      { method: "PATCH", body: { execution_config: { model: null } } },
    ]);
    expect(created.execution_config).toEqual(session.execution_config);
    expect(ChatSessionSchema.parse({ ...session, execution_config_supported: "true" }).execution_config_supported).toBe(false);
    expect(ChatSessionSchema.parse({ id: "legacy" }).execution_config_supported).toBe(false);
    expect(updated.execution_config).toEqual(session.execution_config);
    expect(ChatSessionSchema.parse({ ...session, execution_config: { model: 1, approval_policy: "future-policy" } }).execution_config)
      .toEqual({ model: null, approval_policy: null });
  });

  // Removing the presence check incorrectly clears settings on a rename;
  // omitting the detail cache write strands its staleTime: Infinity query.
  it("patches config in both caches without clearing omitted fields or unread state", () => {
    const qc = new QueryClient();
    qc.setQueryData(chatKeys.sessions("ws-1"), [session, { ...session, id: "chat-2" }]);
    qc.setQueryData(chatKeys.session("ws-1", "chat-1"), session);
    qc.setQueryData(chatKeys.sessions("ws-2"), [session]);
    applyChatSessionUpdatedToCache(qc, "ws-1", { chat_session_id: "chat-1", title: "Renamed" });
    expect(qc.getQueryData<ChatSession>(chatKeys.session("ws-1", "chat-1"))?.execution_config)
      .toEqual(session.execution_config);
    const next = { model: null, thinking_level: "low", approval_policy: "full-access" as const };
    applyChatSessionUpdatedToCache(qc, "ws-1", { chat_session_id: "chat-1", execution_config: next });
    expect(qc.getQueryData<ChatSession>(chatKeys.session("ws-1", "chat-1"))?.execution_config).toEqual(next);
    expect(qc.getQueryData<ChatSession>(chatKeys.session("ws-1", "chat-1"))?.effective_execution_config).toBeUndefined();
    expect(qc.getQueryState(chatKeys.session("ws-1", "chat-1"))?.isInvalidated).toBe(true);
    expect(qc.getQueryState(chatKeys.sessions("ws-1"))?.isInvalidated).toBe(true);
    const rows = qc.getQueryData<ChatSession[]>(chatKeys.sessions("ws-1"))!;
    expect(rows[0]).toMatchObject({ execution_config: next, unread_count: 2, has_unread: true });
    expect(rows[1]?.execution_config).toEqual(session.execution_config);
    expect(qc.getQueryData<ChatSession[]>(chatKeys.sessions("ws-2"))?.[0]?.execution_config).toEqual(session.execution_config);
    qc.clear();
  });
});

describe("unsent chat execution drafts", () => {
  // Removing immutable replacement or the expected-draft guard lets a completed
  // send erase settings chosen while its first-session request was in flight.
  it("retains a send snapshot and only clears the draft that was accepted", async () => {
    const data = new Map<string, string>();
    const storage = {
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => { data.set(key, value); },
      removeItem: (key: string) => { data.delete(key); },
    };
    setCurrentWorkspace("workspace-a", "ws-1");
    await Promise.resolve();
    const store = createChatStore({ storage });
    store.getState().setDraftExecutionConfig("agent-1", { model: "model-a", approval_policy: "on-request" });
    const sent = store.getState().draftExecutionConfigs["agent-1"];
    store.getState().setDraftExecutionConfig("agent-1", { model: "model-b", approval_policy: null });
    store.getState().clearDraftExecutionConfig("agent-1", sent);
    expect(sent).toEqual({ model: "model-a", approval_policy: "on-request" });
    expect(store.getState().draftExecutionConfigs["agent-1"]).toEqual({ model: "model-b", approval_policy: null });
    const reloaded = createChatStore({ storage });
    expect(reloaded.getState().draftExecutionConfigs["agent-1"]).toEqual({ model: "model-b", approval_policy: null });
    setCurrentWorkspace("workspace-b", "ws-2");
    await Promise.resolve();
    expect(store.getState().draftExecutionConfigs).toEqual({});
    store.getState().setDraftExecutionConfig("agent-2", { thinking_level: "low" });
    setCurrentWorkspace("workspace-a", "ws-1");
    await Promise.resolve();
    expect(store.getState().draftExecutionConfigs["agent-2"]).toBeUndefined();
    const accepted = store.getState().draftExecutionConfigs["agent-1"];
    store.getState().clearDraftExecutionConfig("agent-1", accepted);
    expect(createChatStore({ storage }).getState().draftExecutionConfigs).toEqual({});
  });
});
