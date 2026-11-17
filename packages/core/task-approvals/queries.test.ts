// @vitest-environment node
import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "../api/client";
import { setApiInstance } from "../api";
import { parseWithFallback } from "../api/schema";
import { TaskApprovalListSchema } from "../api/schemas";
import { taskApprovalKeys, taskApprovalsOptions } from "./queries";
import type { TaskApproval } from "./types";

const approval = {
  id: "approval-1", task_id: "task-1", runtime_id: "51e2d473-709c-4c42-bd32-9cdb0f983be1",
  dispatched_at: "2026-11-14T05:10:22.123456Z", execution_id: "a198be36-a63f-45d5-aa74-c8f3998a88d7",
  native_request_id: "9007199254740993", kind: "command", method: "item/commandExecution/requestApproval",
  params: { command: "git status", cwd: "/workspace" }, status: "pending", decision: null,
  created_at: "2026-11-14T05:10:23Z", can_decide: true,
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

let qc: QueryClient;
let client: ApiClient;
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;

beforeEach(() => {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client = new ApiClient("https://multica.test");
  setApiInstance(client);
  fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  qc.clear();
  vi.unstubAllGlobals();
});

describe("task approval boundary", () => {
  // Removing scope defaults or affordance normalization exposes requests without a complete execution fence.
  it("preserves the execution scope and closes unknown or incomplete native request affordances", () => {
    const rows = parseWithFallback<TaskApproval[]>(
      [approval, { ...approval, id: "unknown", kind: "new-kind", status: "new-state", decision: "always" },
        { ...approval, id: "missing-permission", can_decide: undefined },
        { ...approval, id: "missing-scope", runtime_id: undefined, dispatched_at: undefined }],
      TaskApprovalListSchema, [], { endpoint: "approvals" },
    );
    expect(rows[0]).toMatchObject({ runtime_id: approval.runtime_id, dispatched_at: approval.dispatched_at,
      execution_id: approval.execution_id, native_request_id: approval.native_request_id, can_decide: true });
    expect(rows[1]).toMatchObject({ kind: "unsupported", status: "expired", decision: null, can_decide: false });
    expect(rows[2]).toMatchObject({ can_decide: false });
    expect(rows[3]).toMatchObject({ runtime_id: "", dispatched_at: "", can_decide: false });
  });

  // The parsed fallback must not be mistaken for a successful empty approval list.
  it("surfaces malformed successful responses instead of silently showing no approvals", async () => {
    fetchMock.mockResolvedValue(json({ approvals: "invalid" }));
    await expect(qc.fetchQuery(taskApprovalsOptions("task-1", "ws-1"))).rejects.toThrow("Invalid task approvals response");
  });

  // A 200 without a persisted decision must not become a successful approval in the UI.
  it("rejects a successful response that does not confirm the requested decision", async () => {
    fetchMock.mockResolvedValue(json(approval));
    await expect(client.decideTaskApproval("task-1", TaskApprovalListSchema.parse([approval])[0]!, "approve", { workspaceId: "ws-1" }))
      .rejects.toThrow("Invalid task approval decision response");
  });

  it("binds requests and caches to their workspace while polling for subsequent native requests", async () => {
    fetchMock.mockResolvedValue(json([approval]));
    const options = taskApprovalsOptions("task-1", "ws-1");
    const rows = await qc.fetchQuery(options);
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({ "X-Workspace-ID": "ws-1", "X-Workspace-Slug": "" });
    expect(qc.getQueryData(taskApprovalKeys.list("task-1", "ws-1"))).toHaveLength(1);
    expect(qc.getQueryData(taskApprovalKeys.list("task-1", "ws-2"))).toBeUndefined();
    const query = qc.getQueryCache().build<TaskApproval[], Error, TaskApproval[], ReturnType<typeof taskApprovalKeys.list>>(qc, options);
    if (typeof options.refetchInterval !== "function") throw new Error("Missing poll policy");
    expect(options.refetchInterval(query)).toBe(1000);
    qc.setQueryData(options.queryKey, rows.map((row) => ({ ...row, status: "processed" as const, can_decide: false })));
    expect(options.refetchInterval(query)).toBe(1000);
  });

  // Removing the error branch would keep requesting an endpoint absent on older servers.
  it("does not retry or keep polling an unavailable endpoint", async () => {
    fetchMock.mockResolvedValue(json({ error: "Not found" }, 404));
    const options = taskApprovalsOptions("task-1");
    await expect(qc.fetchQuery(options)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    if (typeof options.refetchInterval !== "function") throw new Error("Missing poll policy");
    expect(options.refetchInterval(qc.getQueryCache().build<TaskApproval[], Error, TaskApproval[], ReturnType<typeof taskApprovalKeys.list>>(qc, options))).toBe(false);
  });
});
