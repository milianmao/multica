import { queryOptions } from "@tanstack/react-query";
import { api } from "../api";

/** Optional workspace identity follows account-scoped chat queries without sharing workspace caches. */
export const taskApprovalKeys = {
  all: (wsId?: string) => ["task-approvals", wsId ?? null] as const,
  list: (taskId: string, wsId?: string) => [...taskApprovalKeys.all(wsId), taskId] as const,
};

/**
 * Mounted task surfaces discover new requests even after a previous one was processed.
 * Any failed fetch stops polling until explicit retry, including old servers without this endpoint.
 */
export function taskApprovalsOptions(taskId: string, wsId?: string) {
  return queryOptions({
    queryKey: taskApprovalKeys.list(taskId, wsId),
    queryFn: ({ signal }) => api.listTaskApprovals(taskId, wsId ? { workspaceId: wsId } : undefined, signal),
    enabled: !!taskId,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: (query) => query.state.error ? false : 1000,
  });
}
