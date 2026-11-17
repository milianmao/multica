import type { AgentTask, TimelineEntry } from "@multica/core/types";
import { commentSupplementReceipts } from "@multica/core/issues/run-steering";
import { isDeletedComment } from "@multica/core/issues/comment-deletion";
import { groupSteps, type TraceRow, type TraceStep } from "./build-steps";
import { transcriptFinalReply } from "./conversation-result";

/** A published message has its own stable identity, not a fabricated task seq. */
export interface ConversationMessageRow {
  kind: "published";
  id: string;
  content: string;
  startedAt?: string;
  final: boolean;
  entry?: TimelineEntry;
}
export type ConversationRow = TraceRow | ConversationMessageRow;

/** Unknown publication dates remain untimed rather than borrowing the last progress time. */
function publicationTime(row: ConversationMessageRow): number {
  const stamp = Date.parse(row.startedAt ?? "");
  return Number.isFinite(stamp) ? stamp : Infinity;
}

/** Gather only this task's guidance and explicit final, ordered independently of receipt updates. */
export function buildConversationMessages(task: AgentTask, entries: readonly TimelineEntry[]): ConversationMessageRow[] {
  const messages: ConversationMessageRow[] = entries.flatMap((entry) => {
    if (entry.type !== "comment" || isDeletedComment(entry)) return [];
    const receipts = commentSupplementReceipts(entry).filter((receipt) => receipt.task_id === task.id);
    return receipts.length > 0 ? [{ kind: "published", id: "comment:" + entry.id, final: false,
      content: entry.content ?? "", startedAt: entry.created_at, entry: { ...entry, supplements: receipts } }] : [];
  });
  const final = transcriptFinalReply(task, entries);
  if (final) messages.push({ kind: "published", id: "final:" + task.id, final: true,
    content: final.content, startedAt: final.createdAt });
  return messages.sort((a, b) => publicationTime(a) - publicationTime(b)
    || Number(a.final) - Number(b.final) || a.id.localeCompare(b.id));
}

/** Keep stream seq authoritative, inserting published messages at observed time boundaries. */
export function mergeConversationRows(steps: TraceStep[], messages: readonly ConversationMessageRow[]): ConversationRow[] {
  const rows: ConversationRow[] = [];
  let pending: TraceStep[] = [];
  let cursor = 0;
  let clock = -Infinity;
  const flush = () => { rows.push(...groupSteps(pending)); pending = []; };
  for (const step of steps) {
    const stamp = Date.parse(step.startedAt ?? "");
    if (Number.isFinite(stamp)) clock = Math.max(clock, stamp);
    // Equal stamps keep stream seq first; a clock adjustment cannot reorder task events.
    while (cursor < messages.length && publicationTime(messages[cursor]!) < clock) {
      flush();
      rows.push(messages[cursor++]!);
    }
    pending.push(step);
  }
  flush();
  rows.push(...messages.slice(cursor));
  return rows;
}

/** Narrow published rows before applying trace-only selection and tool grouping. */
export function isConversationMessageRow(row: ConversationRow): row is ConversationMessageRow {
  return row.kind === "published";
}
