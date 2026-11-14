import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { taskApprovalKeys } from "./queries";
import type { TaskApproval, TaskApprovalDecision } from "./types";

/**
 * Decisions are never optimistic or retried automatically. Only a confirmed server record
 * replaces cached state. The current row supplies its exact execution fence; refreshing failures catches revoked authority or expiry.
 */
export function useDecideTaskApproval(taskId: string, wsId?: string) {
  const qc = useQueryClient();
  const queryKey = taskApprovalKeys.list(taskId, wsId);
  return useMutation({
    mutationFn: (input: { approval: TaskApproval; decision: TaskApprovalDecision }) =>
      api.decideTaskApproval(taskId, input.approval, input.decision, wsId ? { workspaceId: wsId } : undefined),
    retry: false,
    onSuccess: (approval) => {
      qc.setQueryData<TaskApproval[]>(queryKey, (rows) => rows?.map((row) => row.id === approval.id ? approval : row));
    },
    onSettled: () => qc.invalidateQueries({ queryKey, exact: true }),
  });
}
