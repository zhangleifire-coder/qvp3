-- 产能验证平台 初始 schema（21 张表）
-- 用 psql 直接执行：psql -h localhost -U qvp -d qvp -f migrations/001_initial_schema.sql

CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id),
    name TEXT NOT NULL,
    email TEXT UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('A','B','C','admin')),
    capabilities JSONB NOT NULL DEFAULT '[]',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    password_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT UNIQUE NOT NULL,
    query TEXT NOT NULL,
    content_type TEXT NOT NULL,
    platform TEXT,
    sla_hours INT NOT NULL DEFAULT 24,
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal','urgent','scheduled')),
    status TEXT NOT NULL DEFAULT 'draft',
    template_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by UUID REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks(status);
CREATE INDEX IF NOT EXISTS tasks_created_at_idx ON tasks(created_at);

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'general';

CREATE TABLE IF NOT EXISTS entity_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    version TEXT NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ,
    attributes JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(entity_type, canonical_name, version)
);

CREATE TABLE IF NOT EXISTS claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    claim_text TEXT NOT NULL,
    risk_level TEXT NOT NULL CHECK (risk_level IN ('P0','P1','P2','P3')),
    verification_status TEXT NOT NULL DEFAULT 'pending',
    position INT NOT NULL
);
CREATE INDEX IF NOT EXISTS claims_task_idx ON claims(task_id);

CREATE TABLE IF NOT EXISTS evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    source_level TEXT NOT NULL CHECK (source_level IN ('P0','P1','P2','P3')),
    publish_date DATE,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    excerpt TEXT,
    supports BOOLEAN NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_claim_idx ON evidence(claim_id);

CREATE TABLE IF NOT EXISTS drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version INT NOT NULL,
    body TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    token_count INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_id, version)
);

CREATE TABLE IF NOT EXISTS page_copies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    page_index INT NOT NULL CHECK (page_index BETWEEN 1 AND 6),
    body TEXT NOT NULL,
    claim_ids JSONB NOT NULL DEFAULT '[]',
    UNIQUE(task_id, page_index)
);

CREATE TABLE IF NOT EXISTS assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    page_index INT NOT NULL,
    subject TEXT,
    source_type TEXT NOT NULL CHECK (source_type IN ('official','user_upload','licensed','ai_generated','product_render')),
    copyright_status TEXT NOT NULL CHECK (copyright_status IN ('clear','unknown','restricted')),
    license_scope TEXT,
    hash TEXT NOT NULL,
    image_url TEXT,
    model_version TEXT,
    is_illustration BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS assets_task_idx ON assets(task_id);

CREATE TABLE IF NOT EXISTS ocr_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    raw_text TEXT NOT NULL,
    key_fields JSONB NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rule_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    rule_name TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS rule_results_task_idx ON rule_results(task_id);

CREATE TABLE IF NOT EXISTS cross_checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    expected TEXT NOT NULL,
    actual TEXT NOT NULL,
    matched BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS risk_classifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID UNIQUE NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    level TEXT NOT NULL CHECK (level IN ('green','yellow','red')),
    reasons JSONB NOT NULL DEFAULT '[]',
    classified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS review_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('A','B','C')),
    reviewer_id UUID REFERENCES users(id),
    locked_at TIMESTAMPTZ,
    last_heartbeat_at TIMESTAMPTZ,
    auto_suspended_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    anomaly_flag BOOLEAN NOT NULL DEFAULT FALSE,
    time_inconsistency_flag BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS review_sessions_task_role_idx ON review_sessions(task_id, role);
CREATE UNIQUE INDEX IF NOT EXISTS review_sessions_active_lock_idx
    ON review_sessions(task_id, role)
    WHERE finished_at IS NULL;

CREATE TABLE IF NOT EXISTS review_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_session_id UUID NOT NULL REFERENCES review_sessions(id) ON DELETE CASCADE,
    idempotency_key TEXT UNIQUE NOT NULL,
    action_type TEXT NOT NULL CHECK (action_type IN ('view','approve','reject','transfer','escalate')),
    client_ts TIMESTAMPTZ NOT NULL,
    server_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}',
    duration_ms INT
);

CREATE TABLE IF NOT EXISTS issues (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('A','B','C')),
    priority TEXT NOT NULL CHECK (priority IN ('P0','P1','P2')),
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    created_by UUID REFERENCES users(id),
    closed_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS issues_task_idx ON issues(task_id);

CREATE TABLE IF NOT EXISTS batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id UUID,
    risk_level TEXT NOT NULL CHECK (risk_level IN ('green')),
    sampling_rate REAL NOT NULL DEFAULT 0.20,
    member_count INT NOT NULL,
    signoff_status TEXT NOT NULL DEFAULT 'pending' CHECK (signoff_status IN ('pending','signed','frozen','withdrawn')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS batch_members (
    batch_id UUID NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    sampled BOOLEAN NOT NULL DEFAULT FALSE,
    review_result TEXT CHECK (review_result IN ('passed','failed','pending')),
    PRIMARY KEY (batch_id, task_id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID REFERENCES batches(id),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('A','B','C','release')),
    approver_id UUID REFERENCES users(id),
    conclusion TEXT NOT NULL,
    signed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS publish_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id),
    snapshot_data JSONB NOT NULL,
    immutable BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS node_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    node_name TEXT NOT NULL,
    node_idempotency_key TEXT NOT NULL,
    enqueued_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    model_version TEXT,
    prompt_version TEXT,
    retry_count INT NOT NULL DEFAULT 0,
    cost_estimate_cny NUMERIC(10,4),
    error_class TEXT,
    anomaly_flag BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(task_id, node_name, node_idempotency_key)
);
CREATE INDEX IF NOT EXISTS node_events_task_idx ON node_events(task_id);
CREATE INDEX IF NOT EXISTS node_events_started_idx ON node_events(started_at);
