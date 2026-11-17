import type { AgentTask, TimelineEntry } from "@multica/core/types";
import { isDeletedComment } from "@multica/core/issues/comment-deletion";
import { commentRunOutput } from "../../issues/components/comment-runs";

/** A final answer carries its recorded completion or publication time, never a progress timestamp. */
export function transcriptFinalReply(task: AgentTask, entries: readonly TimelineEntry[]): { content: string; createdAt?: string } | null {
  const result = commentRunOutput(task);
  const published = entries.findLast((entry) => entry.type === "comment"
    && entry.actor_type === "agent" && entry.source_task_id === task.id
    && entry.comment_type !== "system" && !isDeletedComment(entry)
    && !!entry.content?.trim());
  if (result) return { content: result, createdAt: task.completed_at ?? published?.created_at };
  return published ? { content: published.content!, createdAt: published.created_at } : null;
}
