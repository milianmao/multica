-- name: LockTaskApprovalTask :one
-- The task lock serializes native approval writes with cancel, completion and reclaim.
SELECT * FROM agent_task_queue WHERE id = @task_id FOR UPDATE;

-- name: RegisterTaskApproval :one
-- Native IDs may reset on a cold retry of the same claim; execution_id names that specific process.
INSERT INTO task_approval (
    id, task_id, workspace_id, issue_id, runtime_id, dispatched_at, execution_id,
    native_request_id, kind, method, params
) VALUES (
    COALESCE(sqlc.narg('id')::uuid, gen_random_uuid()), @task_id, @workspace_id,
    sqlc.narg('issue_id')::uuid, @runtime_id, @dispatched_at, @execution_id,
    @native_request_id, @kind, @method, @params::json
)
ON CONFLICT (task_id, runtime_id, dispatched_at, execution_id, native_request_id) DO UPDATE
SET native_request_id = EXCLUDED.native_request_id
WHERE task_approval.workspace_id = EXCLUDED.workspace_id
  AND task_approval.kind = EXCLUDED.kind
  AND task_approval.method = EXCLUDED.method
  AND task_approval.params::text = EXCLUDED.params::text
  AND task_approval.status <> 'expired'
RETURNING task_approval.*;

-- name: ListTaskApprovals :many
SELECT * FROM task_approval WHERE task_id = @task_id AND workspace_id = @workspace_id ORDER BY created_at, id;

-- name: ListLiveTaskApprovals :many
SELECT * FROM task_approval
WHERE task_id = @task_id AND workspace_id = @workspace_id AND runtime_id = @runtime_id
  AND dispatched_at = @dispatched_at AND execution_id = @execution_id
  AND status IN ('pending', 'decided')
ORDER BY created_at, id;

-- name: GetTaskApproval :one
SELECT * FROM task_approval WHERE task_id = @task_id AND workspace_id = @workspace_id AND id = @approval_id;

-- name: DecideTaskApproval :one
UPDATE task_approval
SET status = 'decided', decision = @decision::text, decided_by = @decided_by::uuid, decided_at = now()
WHERE task_id = @task_id AND workspace_id = @workspace_id AND id = @approval_id
  AND runtime_id = @runtime_id AND dispatched_at = @dispatched_at AND execution_id = @execution_id
  AND status = 'pending'
RETURNING *;

-- name: AckTaskApproval :one
UPDATE task_approval
SET status = @status::text,
    processed_at = CASE WHEN @status::text = 'processed' THEN now() ELSE NULL END,
    expired_at = CASE WHEN @status::text = 'expired' THEN now() ELSE NULL END
WHERE task_id = @task_id AND workspace_id = @workspace_id AND id = @approval_id
  AND runtime_id = @runtime_id AND dispatched_at = @dispatched_at AND execution_id = @execution_id
  AND ((@status::text = 'processed' AND status = 'decided')
       OR (@status::text = 'expired' AND status IN ('pending', 'decided')))
RETURNING *;

-- name: ExpireTaskApprovalExecution :exec
UPDATE task_approval SET status = 'expired', expired_at = now()
WHERE task_id = @task_id AND runtime_id = @runtime_id
  AND dispatched_at = @dispatched_at AND execution_id = @execution_id
  AND status IN ('pending', 'decided');

-- name: ExpireStaleTaskApprovals :exec
-- Cold-retry executions share a claim; process exit expires its own execution, not another process's requests.
UPDATE task_approval SET status = 'expired', expired_at = now()
WHERE task_id = @task_id AND status IN ('pending', 'decided')
  AND (sqlc.narg('dispatched_at')::timestamptz IS NULL
       OR dispatched_at IS DISTINCT FROM sqlc.narg('dispatched_at')::timestamptz
       OR runtime_id IS DISTINCT FROM sqlc.narg('runtime_id')::uuid);

-- name: ExpireTaskApprovals :exec
-- Call inside the existing terminal/reclaim transaction; processed receipts remain history.
UPDATE task_approval SET status = 'expired', expired_at = now()
WHERE task_id = @task_id AND status IN ('pending', 'decided');

-- name: ExpireTaskApprovalsByTaskIDs :exec
-- Settle terminal tasks in the existing transaction without one query per task.
UPDATE task_approval SET status = 'expired', expired_at = now()
WHERE task_id = ANY(@task_ids::uuid[]) AND status IN ('pending', 'decided');

-- name: DeleteTaskApprovalsByTaskIDs :exec
DELETE FROM task_approval WHERE task_id = ANY(@task_ids::uuid[]);

-- name: DeleteTaskApprovalsByWorkspace :exec
DELETE FROM task_approval WHERE workspace_id = @workspace_id;

-- name: DeleteTaskApprovalsByIssue :exec
DELETE FROM task_approval WHERE issue_id = @issue_id AND workspace_id = @workspace_id;

-- name: DeleteTaskApprovalsByChatSession :exec
-- The caller holds the session deletion lock; task rows must precede approval rows in the lock order.
WITH locked_tasks AS MATERIALIZED (
    SELECT t.id FROM agent_task_queue t
    JOIN chat_session s ON s.id = t.chat_session_id
    WHERE s.id = @chat_session_id AND s.workspace_id = @workspace_id
    ORDER BY t.id
    FOR UPDATE OF t
)
DELETE FROM task_approval a USING locked_tasks t
WHERE a.task_id = t.id AND a.workspace_id = @workspace_id;
