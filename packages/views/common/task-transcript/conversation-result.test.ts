// @vitest-environment node

import { describe, expect, it } from "vitest";
import type { AgentTask, TimelineEntry } from "@multica/core/types";
import { transcriptFinalReply } from "./conversation-result";

const task: AgentTask = {
  id: "run", agent_id: "agent", runtime_id: "", issue_id: "issue", status: "completed",
  priority: 0, dispatched_at: null, started_at: null, completed_at: null,
  result: null, error: null, created_at: "2026-11-14T00:00:00Z",
};
const reply: TimelineEntry = {
  type: "comment", id: "reply", actor_type: "agent", actor_id: "agent",
  source_task_id: task.id, content: "Published answer", created_at: task.created_at,
};

describe("transcriptFinalReply", () => {
  it("prefers the task's explicit deliverable to a linked published reply", () => {
    expect(transcriptFinalReply({ ...task, completed_at: "2026-11-14T00:02:00Z",
      result: { comment: "Task deliverable" } }, [reply]))
      .toEqual({ content: "Task deliverable", createdAt: "2026-11-14T00:02:00Z" });
  });

  it("selects an agent comment by exact source task, not nearby activity", () => {
    expect(transcriptFinalReply(task, [reply,
      { ...reply, id: "other", source_task_id: "other-run", content: "Unrelated answer" },
      { ...reply, id: "member", actor_type: "member", content: "Member input" },
      { ...reply, id: "deleted", deleted_at: task.created_at, content: "Deleted answer" },
      { ...reply, id: "notice", comment_type: "system", content: "Failure notice" },
    ])).toEqual({ content: "Published answer", createdAt: reply.created_at });
  });

  it("leaves a completed run without an explicit answer unlabelled", () => {
    expect(transcriptFinalReply({ ...task, result: { output: "Progress only" } }, []))
      .toBeNull();
  });
});
