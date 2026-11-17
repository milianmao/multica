// @vitest-environment jsdom

import type { ReactNode } from "react";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@multica/core/api";
import { useTranscriptViewStore } from "@multica/core/agents/stores";
import { issueKeys } from "@multica/core/issues/queries";
import type { AgentTask, TimelineEntry } from "@multica/core/types";
import type { TaskMessagePayload } from "@multica/core/types/events";
import { renderWithI18n } from "../../test/i18n";
import { AgentTranscriptDialog } from "./agent-transcript-dialog";
import { buildTimeline } from "./build-timeline";

vi.mock("@multica/core/api", async (original) => ({
  ...await original<typeof import("@multica/core/api")>(),
  api: {
    getAgent: vi.fn(), listRuntimes: vi.fn(), listTimeline: vi.fn(),
    listTasksByIssue: vi.fn(), createComment: vi.fn(), createTaskSupplement: vi.fn(), cancelTask: vi.fn(),
    retryTaskSupplement: vi.fn(),
  },
}));
vi.mock("@multica/core/hooks", () => ({ useWorkspaceId: () => "workspace" }));
vi.mock("@multica/core/workspace/hooks", () => ({
  useActorName: () => ({ getActorName: () => "Codex" }),
}));
vi.mock("./use-trace-issue-labels", () => ({ useTraceIssueLabels: () => (text: string) => text }));
vi.mock("./context-receipt", () => ({ ContextReceiptButton: () => null }));
vi.mock("../task-approval/task-approval-cards", () => ({
  TaskApprovalCards: ({ taskId }: { taskId: string }) => <p data-task-id={taskId}>Approval requested</p>,
}));
vi.mock("../actor-avatar", () => ({ ActorAvatar: () => null }));
const virtualList = vi.hoisted(() => ({ mounted: vi.fn(), rendered: vi.fn(), scrollToIndex: vi.fn() }));
// jsdom has no measured viewport; keep real rows and observe the live-follow contract.
vi.mock("react-virtuoso", async () => {
  const { forwardRef, useEffect, useImperativeHandle } = await import("react");
  return { Virtuoso: forwardRef(function VirtualList(props: {
    data: unknown[]; itemContent: (index: number, row: unknown) => ReactNode;
    computeItemKey: (index: number, row: unknown) => string | number;
    components?: { Footer?: React.ComponentType<{ context?: unknown }> }; context?: unknown;
    followOutput: (atBottom: boolean) => false | "auto";
  }, ref) {
    const { data, itemContent, components, context, computeItemKey } = props;
    virtualList.rendered(props);
    useEffect(() => { virtualList.mounted(); }, []);
    useImperativeHandle(ref, () => ({ scrollToIndex: virtualList.scrollToIndex }));
    const Footer = components?.Footer;
    return <div data-testid="transcript-list">{data.map((row, index) =>
      <div data-testid="transcript-row" key={computeItemKey(index, row)}>{itemContent(index, row)}</div>)}
      {Footer && <Footer context={context} />}</div>;
  }) };
});
vi.mock("@multica/ui/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: ReactNode }) => open ? <>{children}</> : null,
  DialogContent: ({ children }: { children: ReactNode }) => <div role="dialog">{children}</div>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
  // alert-dialog imports it from this module, so the mock must re-export it.
  SafeAreaLayer: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

const task: AgentTask = {
  id: "3eb719ce-6c97-4c09-a953-1c148f022ce9", agent_id: "agent", runtime_id: "",
  issue_id: "issue", status: "running", priority: 0, dispatched_at: null,
  started_at: "2026-11-14T00:00:00Z", completed_at: null, result: null, error: null,
  created_at: "2026-11-14T00:00:00Z", trigger_comment_id: "child-input",
  supplement_capability: "task-supplement-v1", can_supplement: true,
};
const items = buildTimeline([
  { task_id: task.id, issue_id: task.issue_id, seq: 1, type: "text", content: "## Agent update\nWorking on the requested fix." },
  { task_id: task.id, issue_id: task.issue_id, seq: 2, type: "tool_use", tool: "Bash", input: { command: "check-files" } },
  { task_id: task.id, issue_id: task.issue_id, seq: 3, type: "tool_result", tool: "Bash", output: "Verbose tool output" },
]);
const supplement: TimelineEntry = {
  type: "comment", id: "guide", actor_type: "member", actor_id: "member",
  content: "Focus on regression tests", parent_id: "thread-root", created_at: task.created_at,
  supplements: [{ task_id: task.id, agent_id: task.agent_id, status: "pending" }],
};
let client: QueryClient;

function mount(overrides: Partial<AgentTask> = {}, parentId = "thread-root", initialItems = items) {
  const close = vi.fn();
  const dialog = (current: AgentTask, currentItems = initialItems) => <QueryClientProvider client={client}>
    <AgentTranscriptDialog open onOpenChange={close} task={current} items={currentItems}
      agentName="Codex" isLive={current.status === "running"} parentId={parentId} />
  </QueryClientProvider>;
  const result = renderWithI18n(dialog({ ...task, ...overrides }));
  return { ...result, close, update: (current: AgentTask, currentItems = initialItems) => result.rerender(dialog(current, currentItems)) };
}

beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  client.setQueryData(issueKeys.timeline(task.issue_id), []);
  client.setQueryData(issueKeys.tasks(task.issue_id), [task]);
  vi.mocked(api.getAgent).mockResolvedValue(null!);
  vi.mocked(api.listRuntimes).mockResolvedValue([]);
  vi.mocked(api.listTimeline).mockResolvedValue([]);
  vi.mocked(api.listTasksByIssue).mockResolvedValue([task]);
  vi.mocked(api.cancelTask).mockResolvedValue({ ...task, status: "cancelled" });
  vi.mocked(api.retryTaskSupplement).mockResolvedValue(undefined);
  useTranscriptViewStore.setState({ sortDirection: "newest_first", selectedFilterKeys: ["tool:Bash"] });
});
afterEach(() => { cleanup(); client.clear(); });

describe("AgentTranscriptDialog conversation", () => {
  it("merges guidance and final into seq-timed prose without repinning a history reader", async () => {
    const at = (seconds: number) => new Date(Date.parse(task.created_at) + seconds * 1000).toISOString();
    const secret = `ghp_${"A".repeat(36)}`;
    const message = (seq: number, seconds: number, content: string): TaskMessagePayload =>
      ({ task_id: task.id, issue_id: task.issue_id, seq, type: "text", content, created_at: at(seconds) });
    const timeline = buildTimeline([
      message(1, 10, "## Early response\nFirst pass.\n"),
      message(2, 40, `\n## After first guidance\n${secret.slice(0, 22)}`),
      message(3, 45, `${secret.slice(22)}\n\n## After second guidance\nContinue.\n`),
      { task_id: task.id, issue_id: task.issue_id, seq: 4, type: "tool_use", tool: "Bash",
        input: { command: "check-files" }, created_at: at(50) },
      { task_id: task.id, issue_id: task.issue_id, seq: 5, type: "tool_result", tool: "Bash",
        output: "Verbose tool output", created_at: at(55) },
      message(6, 60, "## Before final\nProgress.\n"),
      message(7, 68, "\n## Near completion\nStill progress.\n"),
      message(8, 80, "\n## Late progress\nNot a final answer."),
    ]);
    const guidance = (id: string, seconds: number, content: string): TimelineEntry =>
      ({ ...supplement, id, created_at: at(seconds), content });
    const guides = [guidance("second", 42, "## Second guidance"), guidance("first", 30, "## First guidance")];
    const view = mount({}, "thread-root", timeline);
    await act(async () => { client.setQueryData(issueKeys.timeline(task.issue_id), guides); });
    expect(await screen.findByRole("heading", { name: "First guidance" })).toBeInTheDocument();
    expect(virtualList.rendered.mock.lastCall![0].followOutput(false)).toBe(false);
    expect(virtualList.mounted).toHaveBeenCalledTimes(1);
    expect(virtualList.scrollToIndex).not.toHaveBeenCalled();
    const published: TimelineEntry = { type: "comment", id: "final", actor_type: "agent", actor_id: task.agent_id,
      source_task_id: task.id, created_at: at(70), content: "## Published final" };
    await act(async () => { client.setQueryData(issueKeys.timeline(task.issue_id), [published, ...guides]); });
    view.update({ ...task, status: "completed", completed_at: at(90) });
    await screen.findByRole("heading", { name: "Published final" });
    const rowText = screen.getAllByTestId("transcript-row").map((node) => node.textContent);
    expect(rowText).toEqual(["Early response", "First guidance", "After first guidance", "Second guidance",
      "After second guidance", "check-files", "Near completion", "Published final", "Late progress"]
      .map((label) => expect.stringContaining(label)));
    expect(screen.getByTestId("transcript-list")).toHaveTextContent("[REDACTED GITHUB TOKEN]");
    expect(screen.getByTestId("transcript-list")).not.toHaveTextContent(secret.slice(0, 22));
    expect(screen.queryByText("Verbose tool output")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send as a new run" })).not.toBeInTheDocument();
    expect(api.createComment).not.toHaveBeenCalled();
    view.update({ ...task, status: "completed", completed_at: at(65), result: { comment: "## Explicit final" } });
    await screen.findByRole("heading", { name: "Explicit final" });
    expect(screen.queryByRole("heading", { name: "Published final" })).not.toBeInTheDocument();
    expect(screen.getAllByTestId("transcript-row").map((node) => node.textContent)).toEqual([
      "Early response", "First guidance", "After first guidance", "Second guidance", "After second guidance",
      "check-files", "Before final", "Explicit final", "Near completion",
    ].map((label) => expect.stringContaining(label)));
    expect(virtualList.mounted).toHaveBeenCalledTimes(1);
    expect(virtualList.scrollToIndex).not.toHaveBeenCalled();
  });
  it("defaults to readable prose and compact tools without inheriting log filters", async () => {
    const view = mount();
    await act(async () => {});
    expect(screen.getByRole("button", { name: "Conversation" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: "Agent update" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /check-files/ })).toBeInTheDocument();
    expect(screen.queryByText("Verbose tool output")).not.toBeInTheDocument();
    expect(screen.getByText("Approval requested")).toHaveAttribute("data-task-id", task.id);
    expect(screen.queryByText("Final reply")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Execution log" }));
    expect(screen.queryByRole("heading", { name: "Agent update" })).not.toBeInTheDocument();
    expect(useTranscriptViewStore.getState().selectedFilterKeys).toEqual(["tool:Bash"]);
    expect(screen.getByText("Approval requested")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /check-files/ }));
    expect(screen.getByText("Verbose tool output")).toBeInTheDocument();
    view.update({ ...task, status: "completed", result: { output: "Progress only" } });
    fireEvent.click(screen.getByRole("button", { name: "Conversation" }));
    expect(screen.getByRole("heading", { name: "Agent update" })).toBeInTheDocument();
    expect(screen.queryByText("Final reply")).not.toBeInTheDocument();
  });

  it("shows a source-task reply even when no transcript events were recorded", async () => {
    client.setQueryData(issueKeys.timeline(task.issue_id), [{
      type: "comment", id: "answer", actor_type: "agent", actor_id: task.agent_id,
      source_task_id: task.id, content: "## Published answer", created_at: task.created_at,
    }]);
    renderWithI18n(<QueryClientProvider client={client}><AgentTranscriptDialog open
      onOpenChange={vi.fn()} task={{ ...task, status: "completed" }} items={[]} agentName="Codex" />
    </QueryClientProvider>);
    expect(await screen.findByRole("heading", { name: "Published answer" })).toBeInTheDocument();
    expect(screen.getByText("Final reply")).toBeInTheDocument();
  });

  it("posts text into the exact run and thread and reuses delivery receipts", async () => {
    vi.mocked(api.createTaskSupplement).mockResolvedValue({
      id: supplement.id, issue_id: task.issue_id, author_type: "member", author_id: "member",
      content: supplement.content!, parent_id: supplement.parent_id!, type: "comment",
      created_at: supplement.created_at, updated_at: supplement.created_at, supplements: supplement.supplements,
      reactions: [], attachments: [], resolved_at: null, resolved_by_type: null, resolved_by_id: null,
    });
    mount();
    const input = screen.getByRole("textbox", { name: "Add guidance to this run" });
    fireEvent.change(input, { target: { value: "  Focus on regression tests  " } });
    fireEvent.click(screen.getByRole("button", { name: "Send guidance" }));
    await waitFor(() => expect(api.createTaskSupplement).toHaveBeenCalledWith(
      task.issue_id, task.id, "Focus on regression tests", expect.any(String),
    ));
    await waitFor(() => expect(input).toHaveValue(""));
    expect(await screen.findByText("Waiting for Codex to read it")).toBeInTheDocument();
    await act(async () => { client.setQueryData(issueKeys.timeline(task.issue_id), [{ ...supplement,
      supplements: [{ task_id: task.id, agent_id: task.agent_id, status: "delivered" }],
    }]); });
    expect(await screen.findByText("Read by Codex")).toBeInTheDocument();
    await act(async () => { client.setQueryData(issueKeys.timeline(task.issue_id), [{ ...supplement,
      supplements: [{ task_id: task.id, agent_id: task.agent_id, status: "failed", failure_reason: "timeout" }],
    }]); });
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(api.retryTaskSupplement).toHaveBeenCalledWith(task.issue_id, task.id, supplement.id));
    expect(screen.queryByRole("button", { name: "Send as a new run" })).not.toBeInTheDocument();
  });

  it("keeps failed input when the exact run ends before the send returns", async () => {
    let failSend: (error: Error) => void = () => {};
    vi.mocked(api.createTaskSupplement).mockImplementationOnce(() => new Promise((_, reject) => { failSend = reject; }));
    const view = mount();
    const input = screen.getByRole("textbox", { name: "Add guidance to this run" });
    expect(screen.getByText("Add guidance to this run")).toHaveAttribute("for", input.id);
    expect(input).toHaveAccessibleDescription("Visible in the issue thread.");
    fireEvent.change(input, { target: { value: "Keep this input" } });
    fireEvent.click(screen.getByRole("button", { name: "Send guidance" }));
    await waitFor(() => expect(api.createTaskSupplement).toHaveBeenCalledTimes(1));
    const ended: AgentTask = { ...task, status: "completed", can_supplement: false };
    await act(async () => { client.setQueryData(issueKeys.tasks(task.issue_id), [ended, { ...task, id: "new-run" }]); });
    view.update(ended);
    await act(async () => { failSend(new Error("turn ended")); });
    expect(await screen.findByText("Could not send guidance. Your text was kept.")).toBeInTheDocument();
    expect(input).toHaveValue("Keep this input");
    expect(input).toBeDisabled();
    expect(input).toHaveAccessibleDescription("This run has ended.");
    expect(screen.getByRole("button", { name: "Send guidance" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
    fireEvent.submit(input.closest("form")!);
    expect(api.createTaskSupplement).toHaveBeenCalledTimes(1);
    expect(api.createComment).not.toHaveBeenCalled();
  });

  it("does not offer steering without the run's negotiated capability", async () => {
    mount({ can_supplement: false });
    await act(async () => {});
    expect(screen.getByRole("button", { name: "Send guidance" })).toBeDisabled();
    expect(screen.getByText("This run cannot receive additional guidance.")).toBeInTheDocument();
  });

  it("closes without cancelling and stops only through the original confirmation", async () => {
    const view = mount();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(view.close).toHaveBeenCalledWith(false);
    expect(api.cancelTask).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(screen.getByRole("heading", { name: "Stop this run?" })).toBeInTheDocument();
    expect(api.cancelTask).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Stop run" }));
    await waitFor(() => expect(api.cancelTask).toHaveBeenCalledWith(task.issue_id, task.id));
  });
});
