"use client";

import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError } from "@multica/core/api";
import { taskApprovalsOptions, useDecideTaskApproval, type TaskApproval, type TaskApprovalDecision } from "@multica/core/task-approvals";
import { redactSecrets } from "@multica/core/security/redact-secrets";
import { Button } from "@multica/ui/components/ui/button";
import { useT } from "../../i18n";

export interface TaskApprovalCardsProps {
  taskId: string;
  wsId?: string;
}

/** Recognize structured credential names through the shared redactor, including opaque authorization headers. */
function isCredentialField(field: string): boolean {
  const normalized = field.replace(/([a-z0-9])([A-Z])/g, "$1_$2").replace(/[- ]/g, "_");
  return /authorization|cookie/i.test(normalized) || redactSecrets(normalized + "=value") !== normalized + "=value";
}

/** Redact structured credentials, including JSON file contents embedded as native string parameters. */
function redactApprovalValue(field: string, value: unknown): unknown {
  if (isCredentialField(field)) return "[REDACTED]";
  if (typeof value !== "string") return value;
  const text = redactSecrets(value);
  const trimmed = text.trimStart();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      const parsed: unknown = JSON.parse(text);
      return JSON.stringify(parsed, redactApprovalValue, 2);
    } catch {
      // Diffs and shell commands are not necessarily JSON; the shared text redactor still applies.
    }
  }
  return text;
}

/** Format only already-redacted values; never put raw native params into the DOM. */
function formatApprovalValue(value: unknown, field: string): string {
  const redacted = redactApprovalValue(field, value);
  if (typeof redacted === "string") return redacted;
  return redactSecrets(JSON.stringify(redacted, redactApprovalValue, 2) ?? "");
}

/** One native action: server-owned lifecycle/authority, no optimistic decision and no durable allow-all option. */
function TaskApprovalCard({ approval, taskId, wsId }: TaskApprovalCardsProps & { approval: TaskApproval }) {
  const { t } = useT("common");
  const decide = useDecideTaskApproval(taskId, wsId);
  const submitting = useRef(false);
  const decision = approval.decision;
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const canDecide = approval.status === "pending" && approval.can_decide === true &&
    approval.kind !== "unsupported" && approval.decision === null && !decide.isSuccess;
  const detailLabels: Record<string, string> = {
    command: t(($) => $.task_approvals.command),
    cwd: t(($) => $.task_approvals.cwd),
    changes: t(($) => $.task_approvals.changes),
    permissions: t(($) => $.task_approvals.permissions),
  };

  /** The synchronous latch prevents a second click before React renders the pending state. */
  async function decideOnce(decision: TaskApprovalDecision) {
    if (!canDecide || submitting.current) return;
    submitting.current = true;
    try {
      await decide.mutateAsync({ approval, decision });
      setDecisionError(null);
    } catch (error) {
      setDecisionError(error instanceof Error ? redactSecrets(error.message) : "");
    } finally {
      submitting.current = false;
    }
  }

  return (
    <article className="min-w-0 rounded-lg border bg-muted/20 p-3">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="text-body font-medium">{t(($) => $.task_approvals.kind[approval.kind])}</h3>
        <span role="status" className="text-caption text-muted-foreground">{t(($) => $.task_approvals.status[approval.status])}</span>
      </header>
      {approval.method && <p className="mt-1 break-all font-mono text-caption text-muted-foreground">{redactSecrets(approval.method)}</p>}
      <dl aria-label={t(($) => $.task_approvals.details)} className="mt-2 min-w-0 space-y-2">
        {Object.entries(approval.params).map(([field, value]) => (
          <div key={field} className="min-w-0">
            <dt className="text-caption text-muted-foreground">{detailLabels[field] ?? redactSecrets(field)}</dt>
            <dd className="mt-1 min-w-0">
              <pre className="max-h-48 max-w-full overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 font-mono text-caption">{formatApprovalValue(value, field)}</pre>
            </dd>
          </div>
        ))}
      </dl>
      {decision && <p className="mt-2 text-caption text-muted-foreground">{t(($) => $.task_approvals.decision[decision])}</p>}
      {approval.status === "pending" && !approval.can_decide && approval.kind !== "unsupported" && (
        <p className="mt-2 text-caption text-muted-foreground">{t(($) => $.task_approvals.read_only)}</p>
      )}
      {decisionError !== null && (
        <div role="alert" className="mt-2 text-caption text-destructive">
          <p>{t(($) => $.task_approvals.decision_failed)}</p>
          {decisionError && <p className="break-words">{decisionError}</p>}
        </div>
      )}
      {canDecide && (
        <div className="mt-3 flex flex-wrap items-center gap-2" aria-busy={decide.isPending}>
          <Button type="button" size="sm" disabled={decide.isPending} onClick={() => { void decideOnce("approve"); }}>
            {t(($) => $.task_approvals.approve)}
          </Button>
          <Button type="button" size="sm" variant="outline" disabled={decide.isPending} onClick={() => { void decideOnce("deny"); }}>
            {t(($) => $.task_approvals.deny)}
          </Button>
          {decide.isPending && <span role="status" className="text-caption text-muted-foreground">{t(($) => $.task_approvals.submitting)}</span>}
        </div>
      )}
    </article>
  );
}

/** Shared chat/run surface. Empty lists stay unobtrusive; transport failures remain visible and explicitly retryable. */
export function TaskApprovalCards({ taskId, wsId }: TaskApprovalCardsProps) {
  const { t } = useT("common");
  const query = useQuery(taskApprovalsOptions(taskId, wsId));
  if (!query.data?.length && !query.error) return null;
  const unavailable = query.error instanceof ApiError && [404, 405, 501].includes(query.error.status);
  return (
    <section aria-label={t(($) => $.task_approvals.title)} className="min-w-0 space-y-2">
      {query.error && (
        <div role="alert" className="rounded-lg border border-destructive/30 p-3 text-caption text-destructive">
          <p>{unavailable ? t(($) => $.task_approvals.unavailable) : t(($) => $.task_approvals.load_failed)}</p>
          {!unavailable && <p className="mt-1 break-words">{redactSecrets(query.error.message)}</p>}
          <Button type="button" size="sm" variant="outline" className="mt-2" disabled={query.isFetching} onClick={() => { void query.refetch(); }}>
            {t(($) => $.task_approvals.retry)}
          </Button>
        </div>
      )}
      {query.data?.map((approval) => <TaskApprovalCard key={approval.id} approval={approval} taskId={taskId} wsId={wsId} />)}
    </section>
  );
}
