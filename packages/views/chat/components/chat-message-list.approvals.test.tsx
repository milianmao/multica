// @vitest-environment jsdom
import type { ComponentType, ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient, setApiInstance } from "@multica/core/api";
import { I18nProvider } from "@multica/core/i18n/react";
import { chatKeys } from "@multica/core/chat/queries";
import enChat from "../../locales/en/chat.json";
import enCommon from "../../locales/en/common.json";
import { ChatMessageList } from "./chat-message-list";

// jsdom cannot measure Virtuoso rows. The real stable Footer still receives
// context so this test owns only the chat-to-approval mount, not virtualization.
vi.mock("react-virtuoso", () => ({
  Virtuoso: ({ components, context }: { components: { Footer: ComponentType<{ context: unknown }> }; context: unknown }) => {
    const Footer = components.Footer;
    return <Footer context={context} />;
  },
}));

const taskId = "6af44cbe-80ab-4dfe-b07d-bd3cfd588f4d";
let qc: QueryClient;
afterEach(() => { cleanup(); qc?.clear(); vi.unstubAllGlobals(); });

// Removing the Footer's TaskApprovalCards mount hides native requests until
// trace output arrives; removing its capability gate probes legacy backends.
it("shows one approval before trace output and never probes an unsupported chat", async () => {
  setApiInstance(new ApiClient("https://api.example.test"));
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: unknown) => {
    requests.push(new URL(String(url)).pathname);
    return new Response(JSON.stringify([{
      id: "approval-1", task_id: taskId, runtime_id: "runtime-1",
      dispatched_at: "2026-11-14T00:00:00.123456Z", execution_id: "execution-1",
      native_request_id: "request-1", kind: "command", method: "item/commandExecution/requestApproval",
      params: { command: "pnpm test", cwd: "C:/work" }, status: "pending",
      decision: null, created_at: "2026-11-14T00:00:01Z", decided_at: null, processed_at: null,
      can_decide: true,
    }]), { headers: { "Content-Type": "application/json" } });
  }));
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(chatKeys.taskMessages(taskId), []);
  function Wrapper({ children }: { children: ReactNode }) {
    return <I18nProvider locale="en" resources={{ en: { chat: enChat, common: enCommon } }}><QueryClientProvider client={qc}>{children}</QueryClientProvider></I18nProvider>;
  }
  const list = (enabled: boolean) => <ChatMessageList messages={[]} pendingTask={{ task_id: taskId, status: "running" }} availability={undefined} wsId="ws-1" showTaskApprovals={enabled} />;
  const view = render(list(false), { wrapper: Wrapper });
  await waitFor(() => expect(view.container.querySelector("[data-tab-scroll-root]")).not.toBeNull());
  expect(requests).toEqual([]);
  view.rerender(list(true));
  expect(await screen.findByRole("region", { name: enCommon.task_approvals.title })).toBeTruthy();
  expect(view.container.querySelectorAll("article")).toHaveLength(1);
  expect(requests).toEqual(["/api/tasks/" + taskId + "/approvals"]);
  view.rerender(list(false));
  expect(screen.queryByRole("region", { name: enCommon.task_approvals.title })).toBeNull();
});
