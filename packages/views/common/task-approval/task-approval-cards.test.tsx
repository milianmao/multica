import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient, setApiInstance } from "@multica/core/api";
import { TaskApprovalCards } from "@multica/views/common/task-approval";

const labels = vi.hoisted(() => ({
  title: "Native approvals",
  kind: { command: "Command", file_change: "File changes", permissions: "Permissions", unsupported: "Unsupported request" },
  status: { pending: "Waiting for human approval", decided: "Decided · waiting for execution to continue", processed: "Processed", expired: "Expired" },
  decision: { approve: "Approved once", deny: "Denied" },
  approve: "Approve once", deny: "Deny", submitting: "Submitting decision…",
  read_only: "Only authorized people can decide.", command: "Command", cwd: "Working directory",
  changes: "File changes", permissions: "Permission scope", details: "Request details",
  load_failed: "Could not load approvals.", decision_failed: "Could not submit the decision.",
  unavailable: "Native approvals are unavailable for this task.", retry: "Retry",
}));

vi.mock("../../i18n", () => ({
  useT: () => ({ t: (select: (resource: { task_approvals: typeof labels }) => string) => select({ task_approvals: labels }) }),
}));

const approval = {
  id: "approval-1", task_id: "task-1", runtime_id: "51e2d473-709c-4c42-bd32-9cdb0f983be1",
  dispatched_at: "2026-11-14T05:10:22.123456Z", execution_id: "a198be36-a63f-45d5-aa74-c8f3998a88d7",
  native_request_id: "1", kind: "command", method: "item/commandExecution/requestApproval",
  params: { command: "git status", cwd: "/workspace" }, status: "pending", decision: null,
  created_at: "2026-11-14T05:10:23Z", can_decide: true,
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
let qc: QueryClient;
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;

beforeEach(() => {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  setApiInstance(new ApiClient("https://multica.test"));
  fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  qc.clear();
  vi.unstubAllGlobals();
});
function mount() {
  return render(<QueryClientProvider client={qc}><TaskApprovalCards taskId="task-1" wsId="ws-1" /></QueryClientProvider>);
}
const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");

describe("TaskApprovalCards", () => {
  // The real API parser, query, mutation and Button are used; only transport and translations are replaced.
  it("shows each lifecycle state and redacts command and nested credential details", async () => {
    fetchMock.mockResolvedValue(json([
      { ...approval, params: { command: "curl -H 'Authorization: Bearer hidden-bearer' https://example.com", cwd: "/workspace", token: "hidden-token", headers: { Authorization: "hidden-header" } } },
      { ...approval, id: "file", kind: "file_change", status: "decided", decision: "approve", can_decide: false, params: { changes: { "config.json": { kind: "add", content: JSON.stringify({ password: "hidden-file-credential", keep: "visible" }) } } } },
      { ...approval, id: "permission", kind: "permissions", status: "processed", decision: "deny", can_decide: false, params: { permissions: { network: { allowedDomains: ["example.com"] } } } },
      { ...approval, id: "expired", status: "expired", can_decide: false, params: {} },
    ]));
    mount();
    expect(await screen.findByText(labels.status.pending)).toBeInTheDocument();
    expect(screen.getByText(labels.status.decided)).toBeInTheDocument();
    expect(screen.getByText(labels.status.processed)).toBeInTheDocument();
    expect(screen.getByText(labels.status.expired)).toBeInTheDocument();
    expect(screen.getByText(labels.decision.approve)).toBeInTheDocument();
    expect(screen.getByText(labels.decision.deny)).toBeInTheDocument();
    expect(screen.getByText("/workspace")).toBeInTheDocument();
    expect(screen.getByText(/config.json/)).toBeInTheDocument();
    expect(screen.getByText(/allowedDomains/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: labels.title }).textContent).not.toMatch(/hidden-bearer|hidden-token|hidden-header|hidden-file-credential/);
    expect(screen.getByText(/Bearer \[REDACTED\]/)).toBeInTheDocument();
  });

  it("submits the current row's exact execution scope once and awaits server confirmation", async () => {
    let rows: unknown[] = [approval];
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { finish = resolve; });
    fetchMock.mockImplementation((_url, init) => init?.method === "POST" ? pending : Promise.resolve(json(rows)));
    mount();
    const approve = await screen.findByRole("button", { name: labels.approve });
    fireEvent.click(approve);
    fireEvent.click(approve);
    await waitFor(() => expect(approve).toBeDisabled());
    expect(screen.getByRole("button", { name: labels.deny })).toBeDisabled();
    expect(screen.getByText(labels.status.pending)).toBeInTheDocument();
    expect(posts()).toHaveLength(1);
    expect(posts()[0]?.[1]?.headers).toMatchObject({ "X-Workspace-ID": "ws-1" });
    expect(JSON.parse(String(posts()[0]?.[1]?.body))).toEqual({
      runtime_id: approval.runtime_id, dispatched_at: approval.dispatched_at, execution_id: approval.execution_id, decision: "approve",
    });
    const decided = { ...approval, status: "decided", decision: "approve", can_decide: false };
    rows = [decided];
    finish(json(decided));
    expect(await screen.findByText(labels.status.decided)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: labels.approve })).not.toBeInTheDocument();
  });

  it("retains a failed decision through retry without inventing an approved state", async () => {
    const user = userEvent.setup();
    let rows: unknown[] = [approval];
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { finish = resolve; });
    let attempts = 0;
    fetchMock.mockImplementation((_url, init) => {
      if (init?.method !== "POST") return Promise.resolve(json(rows));
      attempts += 1;
      return attempts === 1 ? Promise.resolve(json({ error: "Write failed: TOKEN=hidden-error" }, 500)) : pending;
    });
    mount();
    await user.click(await screen.findByRole("button", { name: labels.deny }));
    expect(await screen.findByRole("alert")).toHaveTextContent(labels.decision_failed);
    expect(screen.getByRole("alert")).not.toHaveTextContent("hidden-error");
    expect(screen.getByText(labels.status.pending)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: labels.deny }));
    expect(screen.getByRole("alert")).toHaveTextContent(labels.decision_failed);
    const decided = { ...approval, status: "decided", decision: "deny", can_decide: false };
    rows = [decided];
    finish(json(decided));
    expect(await screen.findByText(labels.decision.deny)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(posts()).toHaveLength(2);
    expect(posts().map(([, init]) => JSON.parse(String(init?.body)))).toEqual(Array.from({ length: 2 }, () => ({
      runtime_id: approval.runtime_id, dispatched_at: approval.dispatched_at, execution_id: approval.execution_id, decision: "deny",
    })));
  });

  it("never offers decisions without permission or execution scope, or for unsupported requests", async () => {
    fetchMock.mockResolvedValue(json([
      { ...approval, can_decide: undefined },
      { ...approval, id: "unknown", kind: "future-native-request" },
      { ...approval, id: "missing-scope", runtime_id: undefined, dispatched_at: undefined },
    ]));
    mount();
    expect(await screen.findByText(labels.kind.unsupported)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: labels.approve })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: labels.deny })).not.toBeInTheDocument();
    expect(screen.getAllByText(labels.read_only)).toHaveLength(2);
    expect(posts()).toHaveLength(0);
  });

  it("keeps old-server failures visible and stops automatic requests until manual retry", async () => {
    fetchMock.mockResolvedValue(json({ error: "Not found" }, 404));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent(labels.unavailable);
    await new Promise((resolve) => setTimeout(resolve, 1100));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fetchMock.mockResolvedValue(json([approval]));
    await userEvent.setup().click(screen.getByRole("button", { name: labels.retry }));
    expect(await screen.findByRole("button", { name: labels.approve })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
