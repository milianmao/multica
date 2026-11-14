"use client";

import { useId, useState, type FormEvent } from "react";
import { Loader2, Square } from "@multica/ui/icons";
import { Button } from "@multica/ui/components/ui/button";
import { Textarea } from "@multica/ui/components/ui/textarea";
import { useCancelIssueRun, useCreateTaskSupplement } from "@multica/core/issues/mutations";
import { isSteerableTask } from "@multica/core/issues/run-steering";
import type { AgentTask } from "@multica/core/types";
import { toast } from "sonner";
import { isActiveCommentRun } from "../../issues/components/comment-runs";
import { SteerReceipts } from "../../issues/components/steer-receipts";
import { TerminateTaskConfirmDialog } from "../../issues/components/terminate-task-confirm-dialog";
import { RichContent } from "../../rich-content";
import { useT } from "../../i18n";
import { redactSecrets } from "./redact";
import type { ConversationMessageRow } from "./conversation-timeline";

/** Published rows reuse issue receipts and prose rendering inside the virtualized chronology. */
export function ConversationMessage({ issueId, message }: { issueId: string; message: ConversationMessageRow }) {
  const { t } = useT("agents");
  return <div className="px-4 py-3">
    {message.final ? <section aria-label={t(($) => $.transcript.final_reply)}>
      <h3 className="mb-2 text-caption font-medium text-muted-foreground">{t(($) => $.transcript.final_reply)}</h3>
      <RichContent content={redactSecrets(message.content)} density="compact" className="transcript-prose max-w-[62rem]" />
    </section> : <div className="rounded-lg bg-muted/40 p-3">
      <RichContent content={redactSecrets(message.content)} density="compact" />
      {message.entry && <SteerReceipts issueId={issueId} entry={message.entry} allowResend={false} />}
    </div>}
  </div>;
}

/** Guidance targets this exact turn; a failed send never becomes a follow-up run. */
export function TranscriptRunControls({ task }: { task: AgentTask; parentId?: string }) {
  const { t } = useT("agents");
  const { t: tIssues } = useT("issues");
  const [draft, setDraft] = useState("");
  const [sendFailed, setSendFailed] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const inputId = useId();
  const descriptionId = `${inputId}-description`;
  const send = useCreateTaskSupplement(task.issue_id, task.id);
  const cancel = useCancelIssueRun(task.issue_id);
  const stopping = cancel.isPending || cancel.isSuccess;
  const canSteer = isSteerableTask(task) && !stopping;
  const active = isActiveCommentRun(task);

  /** Recheck the run verdict at submit without substituting another task ID. */
  async function submit(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || !canSteer || send.isPending) return;
    setSendFailed(false);
    try {
      await send.mutateAsync({ content });
      setDraft("");
    } catch {
      setSendFailed(true);
    }
  }

  if (!active && !draft && !send.isPending) return null;

  return <>
    <form onSubmit={(event) => void submit(event)} className="space-y-2">
      <label htmlFor={inputId} className="text-caption font-medium text-muted-foreground">
        {t(($) => $.transcript.guidance_label)}
      </label>
      <Textarea id={inputId} value={draft} onChange={(event) => setDraft(event.target.value)}
        aria-describedby={descriptionId}
        disabled={!canSteer || send.isPending} rows={2} className="max-h-32 min-h-16 resize-y text-body" />
      {sendFailed && <p role="alert" className="text-caption text-destructive">{t(($) => $.transcript.guidance_failed)}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <p id={descriptionId} className="min-w-0 flex-1 text-caption text-muted-foreground">
          {stopping ? tIssues(($) => $.inline_run.stopping)
            : !active ? t(($) => $.transcript.guidance_ended)
              : !canSteer ? t(($) => $.transcript.guidance_unavailable)
                : t(($) => $.transcript.guidance_shared)}
        </p>
        {active && <Button type="button" variant="outline" size="sm" disabled={stopping}
          aria-busy={cancel.isPending} onClick={() => setConfirmStop(true)}>
          {stopping ? <Loader2 aria-hidden className="size-3.5 motion-safe:animate-spin" /> : <Square aria-hidden className="size-3.5" />}
          {stopping ? tIssues(($) => $.inline_run.stopping) : tIssues(($) => $.inline_run.stop)}
        </Button>}
        <Button type="submit" size="sm" disabled={!canSteer || !draft.trim() || send.isPending} aria-busy={send.isPending}>
          {send.isPending && <Loader2 aria-hidden className="size-3.5 motion-safe:animate-spin" />}
          {t(($) => $.transcript.guidance_send)}
        </Button>
      </div>
    </form>
    <TerminateTaskConfirmDialog open={confirmStop} onOpenChange={setConfirmStop}
      showRunningNote={task.status !== "queued"}
      onConfirm={() => {
        if (!active || stopping) return;
        cancel.mutate(task.id, { onError: () => toast.error(tIssues(($) => $.execution_log.cancel_failed)) });
      }} />
  </>;
}
