// @vitest-environment jsdom
import { forwardRef, useImperativeHandle } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@multica/core/i18n/react";
import { ApiClient, setApiInstance } from "@multica/core/api";
import { createChatStore, registerChatStore } from "@multica/core/chat";
import { chatKeys } from "@multica/core/chat/queries";
import { agentListOptions, memberListOptions } from "@multica/core/workspace/queries";
import { projectListOptions } from "@multica/core/projects/queries";
import { agentTaskSnapshotOptions } from "@multica/core/agents/queries";
import { runtimeListOptions, runtimeModelsKeys } from "@multica/core/runtimes";
import { setCurrentWorkspace } from "@multica/core/platform";
import type { Agent, AgentRuntime, ChatSession } from "@multica/core/types";
import type { ChatExecutionConfig } from "@multica/core/types/chat";
import enChat from "../../locales/en/chat.json";
import enAgents from "../../locales/en/agents.json";
import enCommon from "../../locales/en/common.json";
import enEditor from "../../locales/en/editor.json";
import enIssues from "../../locales/en/issues.json";
import { ChatInput } from "./chat-input";
import { ChatExecutionControls } from "./chat-execution-controls";
import { useChatController } from "./use-chat-controller";

vi.mock("@multica/core/hooks", async (original) => ({
  ...(await original<typeof import("@multica/core/hooks")>()), useWorkspaceId: () => "ws-1",
}));
vi.mock("@multica/core/auth", async (original) => {
  const state = { user: { id: "user-1" } };
  return { ...(await original<typeof import("@multica/core/auth")>()),
    useAuthStore: Object.assign((selector: (value: typeof state) => unknown) => selector(state), { getState: () => state }) };
});
vi.mock("../../editor", async () => ({
  ...(await vi.importActual<typeof import("../../editor/use-upload-gate")>("../../editor/use-upload-gate")),
  ...(await vi.importActual<typeof import("../../editor/use-composer-submit")>("../../editor/use-composer-submit")),
  useFileDropZone: () => ({ isDragOver: false, dropZoneProps: {} }), FileDropOverlay: () => null,
  ContentEditor: forwardRef(function Editor(_props, ref) {
    useImperativeHandle(ref, () => ({ getMarkdown: () => "", hasActiveUploads: () => false }));
    return <div role="textbox" aria-label="Message" />;
  }),
}));

const copy = enChat.execution_config;
const modelName = (value: string) => enAgents.pickers.model_tooltip.replace("{{value}}", value);
const thinkingName = (value: string) => enAgents.pickers.thinking_tooltip.replace("{{value}}", value);
const approvalName = (value: string) => copy.approval + " · " + value;
const resources = { en: { chat: enChat, agents: enAgents, common: enCommon, editor: enEditor, issues: enIssues } };
const agent = { id: "agent-1", workspace_id: "ws-1", runtime_id: "runtime-1", name: "Codex", model: "model-a", thinking_level: "high", owner_id: "user-1", permission_mode: "private", archived_at: null } as Agent;
const runtime = { id: "runtime-1", workspace_id: "ws-1", provider: "codex", status: "online", owner_id: "user-1", metadata: {} } as AgentRuntime;
let session: ChatSession;
let qc: QueryClient;
let patches: ChatExecutionConfig[];

function Footer() {
  const controller = useChatController({ isActive: false });
  return <ChatInput onSend={() => {}} uploadEnabled={false} leftAdornment={<ChatExecutionControls controller={controller} />} />;
}

beforeEach(async () => {
  await act(async () => setCurrentWorkspace("workspace", "ws-1"));
  registerChatStore(createChatStore({ storage: { getItem: () => null, setItem: () => {}, removeItem: () => {} } }));
  const { useChatStore } = await import("@multica/core/chat");
  useChatStore.setState({ activeSessionId: "chat-1" });
  session = {
    id: "chat-1", workspace_id: "ws-1", agent_id: agent.id, creator_id: "user-1", title: "Chat", status: "active", has_unread: false,
    created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T00:00:00Z",
    execution_config_supported: true, execution_config_capabilities: { model: true, thinking_level: true, service_tier: true, approval_policy: true },
    execution_config: { model: "model-a", thinking_level: "high", approval_policy: null },
  };
  setApiInstance(new ApiClient("https://api.example.test"));
  patches = [];
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  qc.setQueryData(agentListOptions("ws-1").queryKey, [agent]);
  qc.setQueryData(memberListOptions("ws-1").queryKey, [{ id: "member-1", workspace_id: "ws-1", user_id: "user-1", role: "owner", created_at: session.created_at, name: "User", email: "user@example.test", avatar_url: null }]);
  qc.setQueryData(projectListOptions("ws-1").queryKey, { projects: [], total: 0 });
  qc.setQueryData(runtimeListOptions("ws-1").queryKey, [runtime]);
  qc.setQueryData(agentTaskSnapshotOptions("ws-1").queryKey, []);
  qc.setQueryData(chatKeys.sessions("ws-1"), [session]);
  qc.setQueryData(chatKeys.session("ws-1", session.id), session);
  qc.setQueryData(chatKeys.messagesPage(session.id), { pages: [{ messages: [], has_more: false }], pageParams: [undefined] });
  qc.setQueryData(chatKeys.pendingTask(session.id), null);
  qc.setQueryData(runtimeModelsKeys.forRuntime(runtime.id), { supported: true, cached: false, unavailableModels: [], models: [
    { id: "model-a", label: "A", thinking: { supported_levels: [{ value: "high", label: "High" }] } },
  ] });
  vi.stubGlobal("fetch", vi.fn(async (url: unknown, init?: RequestInit) => {
    const path = new URL(String(url)).pathname;
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body)) as { execution_config: ChatExecutionConfig };
      patches.push(body.execution_config);
      session = { ...session, execution_config: { ...session.execution_config, ...body.execution_config } };
      return new Response(JSON.stringify(session));
    }
    return new Response(JSON.stringify(path === "/api/chat/sessions" ? [session] : { messages: [], restores: [] }));
  }));
});
afterEach(async () => { cleanup(); qc.clear(); vi.unstubAllGlobals(); await act(async () => setCurrentWorkspace(null, null)); });

describe("chat composer execution footer", () => {
  // Removing any directly mounted picker hides an entrance behind the old
  // aggregate popover; catalog capability loss must retain disabled entrances.
  it("exposes three direct entrances with real API writes and visible capability restrictions", async () => {
    const user = userEvent.setup();
    const view = render(<QueryClientProvider client={qc}><I18nProvider locale="en" resources={resources}><Footer /></I18nProvider></QueryClientProvider>);
    const surface = view.container.querySelector('[data-slot="chat-input-surface"]')!;
    const footer = within(surface as HTMLElement);
    const model = footer.getByRole("button", { name: modelName("model-a") });
    const thinking = footer.getByRole("button", { name: thinkingName("High") });
    const approval = footer.getByRole("combobox", { name: approvalName(copy.approval_inherit) });
    expect(model).toBeEnabled(); expect(thinking).toBeEnabled(); expect(approval).toBeEnabled();
    expect(footer.queryByRole("button", { name: copy.label })).toBeNull();
    await user.click(model);
    await user.click(await screen.findByRole("button", { name: copy.inherit }));
    await waitFor(() => expect(patches[0]?.model).toBeNull());
    await waitFor(() => expect(footer.getByRole("button", { name: modelName(copy.inherit) })).toBeEnabled());
    footer.getByRole("combobox", { name: approvalName(copy.approval_inherit) }).focus();
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("option", { name: copy.approval_on_request }));
    await waitFor(() => expect(patches[1]).toEqual({ approval_policy: "on-request" }));
    await waitFor(() => expect(footer.getByRole("combobox", { name: approvalName(copy.approval_on_request) })).toBeEnabled());
    act(() => qc.setQueryData(runtimeModelsKeys.forRuntime(runtime.id), { supported: false, models: [], unavailableModels: [] }));
    await waitFor(() => expect(footer.getByRole("button", { name: modelName(copy.inherit) })).toBeDisabled());
    expect(footer.getByText(copy.catalog_unavailable)).toBeVisible();
    act(() => {
      session = { ...session, execution_config_supported: false };
      qc.setQueryData(chatKeys.sessions("ws-1"), [session]);
      qc.setQueryData(chatKeys.session("ws-1", session.id), session);
    });
    await waitFor(() => expect(footer.getByRole("combobox", { name: approvalName(copy.approval_on_request) })).toBeDisabled());
    expect(within(footer.getByRole("group", { name: copy.thinking })).getByRole("button")).toBeDisabled();
    expect(footer.getByText(copy.unsupported)).toBeVisible();
  });
});
