-- Session-local overrides never mutate the shared agent. Empty objects inherit
-- existing behavior, including approval behavior for clients predating this API.
ALTER TABLE chat_session
ADD COLUMN execution_config JSONB NOT NULL DEFAULT '{}'::jsonb;
