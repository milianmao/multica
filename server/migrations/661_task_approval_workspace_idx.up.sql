CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_approval_workspace
    ON task_approval (workspace_id);
