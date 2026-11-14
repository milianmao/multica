CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_task_approval_id
    ON task_approval (id);
