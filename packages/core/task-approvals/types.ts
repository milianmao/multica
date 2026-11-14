export type TaskApprovalKind = "command" | "file_change" | "permissions" | "unsupported";
export type TaskApprovalStatus = "pending" | "decided" | "processed" | "expired";
export type TaskApprovalDecision = "approve" | "deny";

/** A server-owned request fenced by runtime, exact PostgreSQL dispatch timestamp and opaque execution ID. */
export interface TaskApproval {
  id: string;
  task_id: string;
  runtime_id: string;
  dispatched_at: string;
  execution_id: string;
  native_request_id: string;
  kind: TaskApprovalKind;
  method: string;
  params: Record<string, unknown>;
  status: TaskApprovalStatus;
  decision: TaskApprovalDecision | null;
  created_at: string;
  decided_at: string | null;
  processed_at: string | null;
  can_decide: boolean;
}
