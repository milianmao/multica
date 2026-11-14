-- Codex native approvals are private task data, not connected-app action requests.
-- No foreign keys or implicit indexes: application teardown removes these rows.
CREATE TABLE IF NOT EXISTS task_approval (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    issue_id UUID,
    runtime_id UUID NOT NULL,
    dispatched_at TIMESTAMPTZ NOT NULL,
    execution_id UUID NOT NULL,
    native_request_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('command', 'file_change', 'permissions')),
    method TEXT NOT NULL,
    -- Only redacted display parameters are stored; JSON avoids JSONB escaped-NUL rejection.
    params JSON NOT NULL CHECK (json_typeof(params) = 'object'),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'decided', 'processed', 'expired')),
    decision TEXT CHECK (decision IN ('approve', 'deny')),
    decided_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    expired_at TIMESTAMPTZ,
    CHECK (
        (status = 'pending' AND decision IS NULL AND decided_by IS NULL AND decided_at IS NULL)
        OR (status IN ('decided', 'processed') AND decision IS NOT NULL AND decided_by IS NOT NULL AND decided_at IS NOT NULL)
        OR status = 'expired'
    ),
    CHECK ((status = 'processed') = (processed_at IS NOT NULL)),
    CHECK ((status = 'expired') = (expired_at IS NOT NULL))
);
