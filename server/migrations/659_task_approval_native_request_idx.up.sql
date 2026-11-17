CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_task_approval_native_request
    ON task_approval (task_id, runtime_id, dispatched_at, execution_id, native_request_id);
