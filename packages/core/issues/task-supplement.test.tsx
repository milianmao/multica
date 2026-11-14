// @vitest-environment jsdom

import type { ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient, setApiInstance } from "../api";
import type { Comment, TimelineEntry } from "../types";
import { useCreateTaskSupplement } from "./mutations";
import { issueKeys } from "./queries";

vi.mock("../hooks", () => ({ useWorkspaceId: () => "workspace" }));

const ISSUE_ID = "issue-1";
const TASK_ID = "3eb719ce-6c97-4c09-a953-1c148f022ce9";
const OTHER_TASK_ID = "5e22316c-076c-4d13-b372-aa3e17cd02fd";
const url = "https://multica.test/api/issues/" + ISSUE_ID + "/tasks/" + TASK_ID + "/supplements";
const comment: Comment = {
  id: "guidance", issue_id: ISSUE_ID, author_type: "member", author_id: "member",
  content: "Focus on regression tests", type: "comment", parent_id: "task-trigger",
  created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T00:00:00Z",
  reactions: [], attachments: [], resolved_at: null, resolved_by_type: null, resolved_by_id: null,
  supplements: [{ task_id: TASK_ID, agent_id: "second-agent", status: "pending" }],
};
const sibling: TimelineEntry = {
  type: "comment", id: "sibling", parent_id: "other-thread", content: "Other conversation",
  actor_type: "member", actor_id: "member", created_at: comment.created_at,
};
const fetchMock = vi.fn<typeof fetch>();
let qc: QueryClient;

function Wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
function response(body: unknown = comment, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
function requestBody(index: number): { content: string; client_request_id: string } {
  return JSON.parse(String(fetchMock.mock.calls[index]![1]!.body));
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  setApiInstance(new ApiClient("https://multica.test"));
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  qc.setQueryData<TimelineEntry[]>(issueKeys.timeline(ISSUE_ID), [sibling]);
  qc.setQueryData(issueKeys.tasks(ISSUE_ID), [
    { id: OTHER_TASK_ID, agent_id: "first-agent" }, { id: TASK_ID, agent_id: "second-agent" },
  ]);
  qc.setQueryData(issueKeys.commentTriggerPreview(ISSUE_ID), {});
  qc.setQueryData<TimelineEntry[]>(issueKeys.timeline("other-issue"), [sibling]);
  qc.setQueryData(issueKeys.tasks("other-issue"), []);
});
afterEach(() => { cleanup(); qc.clear(); vi.unstubAllGlobals(); });

describe("strict task supplements", () => {
  it("posts only to the exact run and merges its receipt without replacing sibling threads", async () => {
    const broadcast: TimelineEntry = {
      type: "comment", id: comment.id, actor_type: comment.author_type, actor_id: comment.author_id,
      content: comment.content, parent_id: comment.parent_id, created_at: comment.created_at,
    };
    qc.setQueryData<TimelineEntry[]>(issueKeys.timeline(ISSUE_ID), [sibling, broadcast]);
    const before = qc.getQueryData<TimelineEntry[]>(issueKeys.timeline(ISSUE_ID))!;
    const other = qc.getQueryData(issueKeys.timeline("other-issue"));
    fetchMock.mockResolvedValueOnce(response());
    const { result } = renderHook(() => useCreateTaskSupplement(ISSUE_ID, TASK_ID), { wrapper: Wrapper });

    await act(async () => { await result.current.mutateAsync({ content: comment.content }); });

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(url, expect.objectContaining({ method: "POST" }));
    expect(requestBody(0)).toEqual({ content: comment.content, client_request_id: expect.any(String) });
    expect(requestBody(0).client_request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
    const timeline = qc.getQueryData<TimelineEntry[]>(issueKeys.timeline(ISSUE_ID))!;
    expect(timeline.filter(entry => entry.id === comment.id)).toEqual([
      expect.objectContaining({ parent_id: "task-trigger", supplements: comment.supplements }),
    ]);
    expect(timeline[0]).toBe(before[0]);
    expect(qc.getQueryData(issueKeys.timeline("other-issue"))).toBe(other);
    expect(qc.getQueryState(issueKeys.tasks(ISSUE_ID))?.isInvalidated).toBe(true);
    expect(qc.getQueryState(issueKeys.commentTriggerPreview(ISSUE_ID))?.isInvalidated).toBe(true);
    expect(qc.getQueryState(issueKeys.tasks("other-issue"))?.isInvalidated).toBe(false);
  });

  it("rejects a terminal 409 without creating a plain comment or selecting another run", async () => {
    fetchMock.mockResolvedValueOnce(response({ error: "this run has ended", code: "task_supplement_turn_ended" }, 409));
    const before = qc.getQueryData(issueKeys.timeline(ISSUE_ID));
    const tasks = qc.getQueryData(issueKeys.tasks(ISSUE_ID));
    const { result } = renderHook(() => useCreateTaskSupplement(ISSUE_ID, TASK_ID), { wrapper: Wrapper });

    await act(async () => {
      await expect(result.current.mutateAsync({ content: comment.content })).rejects.toMatchObject({ status: 409 });
    });

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(url, expect.objectContaining({ method: "POST" }));
    expect(qc.getQueryData(issueKeys.timeline(ISSUE_ID))).toBe(before);
    expect(qc.getQueryData(issueKeys.tasks(ISSUE_ID))).toBe(tasks);
    expect(qc.getQueryState(issueKeys.tasks(ISSUE_ID))?.isInvalidated).toBe(false);
  });

  it("does not acknowledge malformed comments or receipts for a different task", async () => {
    fetchMock.mockResolvedValueOnce(response({ ...comment, content: { text: comment.content } }))
      .mockResolvedValueOnce(response({ ...comment,
        supplements: [{ task_id: OTHER_TASK_ID, agent_id: "first-agent", status: "pending" }],
      }))
      .mockResolvedValueOnce(response());
    const before = qc.getQueryData(issueKeys.timeline(ISSUE_ID));
    const { result } = renderHook(() => useCreateTaskSupplement(ISSUE_ID, TASK_ID), { wrapper: Wrapper });

    await act(async () => {
      await expect(result.current.mutateAsync({ content: comment.content })).rejects.toThrow("Target run did not acknowledge the additional message");
    });
    expect(qc.getQueryData(issueKeys.timeline(ISSUE_ID))).toBe(before);
    await act(async () => {
      await expect(result.current.mutateAsync({ content: comment.content })).rejects.toThrow("Target run did not acknowledge the additional message");
    });
    expect(qc.getQueryData(issueKeys.timeline(ISSUE_ID))).toBe(before);
    await act(async () => { await result.current.mutateAsync({ content: comment.content }); });

    expect(fetchMock.mock.calls.map(([calledUrl]) => calledUrl)).toEqual([url, url, url]);
    expect(requestBody(1)).toEqual(requestBody(0));
    expect(requestBody(2)).toEqual(requestBody(0));
    expect(qc.getQueryData<TimelineEntry[]>(issueKeys.timeline(ISSUE_ID))?.find(entry => entry.id === comment.id)?.supplements).toEqual(comment.supplements);
  });

  it("reuses the UUID after a lost response, then renews it for a new or edited send", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("connection lost"))
      .mockResolvedValueOnce(response())
      .mockRejectedValueOnce(new TypeError("connection lost"))
      .mockResolvedValueOnce(response({ ...comment, id: "edited-guidance", content: "Edited guidance" }));
    const { result } = renderHook(() => useCreateTaskSupplement(ISSUE_ID, TASK_ID), { wrapper: Wrapper });

    await act(async () => {
      await expect(result.current.mutateAsync({ content: comment.content })).rejects.toThrow("connection lost");
    });
    await act(async () => { await result.current.mutateAsync({ content: comment.content }); });
    expect(requestBody(1)).toEqual(requestBody(0));
    expect(qc.getQueryData<TimelineEntry[]>(issueKeys.timeline(ISSUE_ID))?.filter(entry => entry.id === comment.id)).toHaveLength(1);
    await act(async () => {
      await expect(result.current.mutateAsync({ content: comment.content })).rejects.toThrow("connection lost");
    });
    await act(async () => { await result.current.mutateAsync({ content: "Edited guidance" }); });

    expect(requestBody(2).client_request_id).not.toBe(requestBody(1).client_request_id);
    expect(requestBody(3).client_request_id).not.toBe(requestBody(2).client_request_id);
    expect(requestBody(3).content).toBe("Edited guidance");
    expect(fetchMock.mock.calls.map(([calledUrl]) => calledUrl)).toEqual([url, url, url, url]);
  });
});
