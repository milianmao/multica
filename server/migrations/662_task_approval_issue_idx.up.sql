CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_approval_issue
    ON task_approval (issue_id) WHERE issue_id IS NOT NULL;
