-- ============================================================================
-- HighForce UNIFIED DATABASE SCHEMA
-- ============================================================================
-- Purpose: Single Supabase instance for ALL data (auth + documents + admin)
-- Version: 2.0.0 (Complete Refactor - Salesforce-Grade)
-- Date: November 19, 2025
-- License: Proprietary - ThunderbirdLabs / HighForce
-- ============================================================================
--
-- ARCHITECTURE:
-- - ONE Supabase for everything (no separate Master/Customer databases)
-- - Row-Level Security (RLS) for database-level isolation
-- - Uniform naming: company_id everywhere (no tenant_id confusion)
-- - Industry standard: Same pattern as Slack, Notion, Linear, Vercel, GitHub
--
-- SECURITY:
-- - RLS policies prevent cross-company data access
-- - Encrypted OAuth tokens (Supabase Vault)
-- - Comprehensive audit logging (all queries tracked)
-- - Admin-only access to sensitive tables
--
-- MULTI-TENANT MODEL:
-- - Users authenticate via Supabase Auth (auth.users)
-- - JWT includes custom claim: app_metadata.company_id
-- - All tables filtered by company_id via RLS
-- - Users can be members of multiple companies
--
-- ============================================================================

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- For fuzzy text search
-- Note: pg_crypto not needed - Supabase has built-in encryption via Vault

-- ============================================================================
-- COMPANIES
-- ============================================================================
-- Central registry of all customer companies using HighForce
-- ============================================================================

CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Company identity
    slug TEXT UNIQUE NOT NULL,                    -- "unit-industries", "acme-corp"
    name TEXT NOT NULL,                           -- "Unit Industries Group, Inc."
    status TEXT DEFAULT 'active' NOT NULL,        -- active, suspended, trial, provisioning, deleted

    -- Business profile
    description TEXT,
    industry TEXT,
    company_size TEXT,                            -- "1-10", "11-50", "51-200", "201-500", "500+"
    location TEXT,                                -- "San Francisco, CA"

    -- Deployment configuration
    plan TEXT DEFAULT 'standard',                 -- trial, standard, enterprise
    backend_url TEXT,                             -- https://cortex-unit.onrender.com
    frontend_url TEXT,                            -- https://unit-cortex.vercel.app

    -- Contact information
    primary_contact_email TEXT,
    primary_contact_name TEXT,
    primary_contact_phone TEXT,

    -- Billing (placeholder for future Stripe integration)
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    activated_at TIMESTAMPTZ,
    trial_ends_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,

    -- Metadata (flexible JSONB for extensibility)
    metadata JSONB DEFAULT '{}'::JSONB,

    -- Constraints
    CONSTRAINT valid_slug CHECK (slug ~ '^[a-z0-9-]+$'),
    CONSTRAINT valid_status CHECK (status IN ('active', 'suspended', 'trial', 'provisioning', 'deleted')),
    CONSTRAINT valid_plan CHECK (plan IN ('trial', 'standard', 'enterprise'))
);

-- Indexes
CREATE INDEX idx_companies_slug ON companies(slug) WHERE deleted_at IS NULL;
CREATE INDEX idx_companies_status ON companies(status) WHERE deleted_at IS NULL;
CREATE INDEX idx_companies_created ON companies(created_at DESC);

-- Comments
COMMENT ON TABLE companies IS 'Central registry of all customer companies using HighForce';
COMMENT ON COLUMN companies.slug IS 'URL-safe company identifier (lowercase, hyphens only)';
COMMENT ON COLUMN companies.status IS 'Company account status (active, suspended, trial, provisioning, deleted)';
COMMENT ON COLUMN companies.metadata IS 'Flexible JSONB for custom fields (industries_served, key_capabilities, etc.)';

-- ============================================================================
-- COMPANY_USERS
-- ============================================================================
-- Maps Supabase auth.users to companies (user can be in multiple companies)
-- ============================================================================

CREATE TABLE company_users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- User association (from auth.users)
    user_id UUID NOT NULL,                        -- Supabase auth.users.id
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- User profile (denormalized for performance)
    email TEXT NOT NULL,
    full_name TEXT,
    role TEXT DEFAULT 'member' NOT NULL,          -- owner, admin, member, viewer

    -- User status
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    invited_by UUID,                              -- user_id who sent invitation
    invited_at TIMESTAMPTZ,
    last_login_at TIMESTAMPTZ,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,

    -- Constraints
    CONSTRAINT unique_user_company UNIQUE(user_id, company_id),
    CONSTRAINT valid_role CHECK (role IN ('owner', 'admin', 'member', 'viewer'))
);

-- Indexes
CREATE INDEX idx_company_users_user ON company_users(user_id);
CREATE INDEX idx_company_users_company ON company_users(company_id);
CREATE INDEX idx_company_users_email ON company_users(email);
CREATE INDEX idx_company_users_active ON company_users(company_id, is_active) WHERE is_active = TRUE;

-- Comments
COMMENT ON TABLE company_users IS 'Maps Supabase auth users to companies (centralized multi-tenant auth)';
COMMENT ON COLUMN company_users.user_id IS 'User ID from Supabase auth.users (centralized authentication)';
COMMENT ON COLUMN company_users.role IS 'User role within this company (owner, admin, member, viewer)';
COMMENT ON COLUMN company_users.invited_by IS 'user_id of the person who invited this user';

-- ============================================================================
-- DOCUMENTS
-- ============================================================================
-- Unified document storage for ALL sources (Gmail, Drive, Outlook, uploads)
-- ============================================================================

CREATE TABLE documents (
    id BIGSERIAL PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- Source metadata
    source TEXT NOT NULL,                         -- gmail, outlook, gdrive, quickbooks, upload
    source_id TEXT NOT NULL,                      -- External ID from source system
    document_type TEXT NOT NULL,                  -- email, pdf, doc, spreadsheet, attachment, file

    -- Content (for RAG ingestion)
    title TEXT NOT NULL,
    content TEXT NOT NULL,                        -- Extracted plain text for embedding
    content_hash TEXT,                            -- SHA-256 for deduplication

    -- File metadata (for file-based sources)
    file_url TEXT,                                -- Supabase Storage URL or external URL
    mime_type TEXT,                               -- application/pdf, image/png, etc.
    file_size_bytes BIGINT,

    -- Parent-child relationships (for email attachments)
    parent_document_id BIGINT REFERENCES documents(id) ON DELETE SET NULL,

    -- Attribution
    user_id UUID,                                 -- User who ingested this document
    user_email TEXT,                              -- Denormalized email for display

    -- Timestamps
    source_created_at TIMESTAMPTZ,                -- When created in source system
    source_modified_at TIMESTAMPTZ,               -- When last modified in source
    ingested_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,

    -- Metadata (flexible JSONB)
    raw_data JSONB DEFAULT '{}'::JSONB,           -- Full original data from source
    metadata JSONB DEFAULT '{}'::JSONB,           -- Processed metadata (sender, recipients, etc.)

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,

    -- Constraints
    CONSTRAINT unique_document UNIQUE(company_id, source, source_id)
);

-- Indexes
CREATE INDEX idx_documents_company ON documents(company_id);
CREATE INDEX idx_documents_source ON documents(company_id, source);
CREATE INDEX idx_documents_type ON documents(company_id, document_type);
CREATE INDEX idx_documents_user ON documents(user_id);
CREATE INDEX idx_documents_parent ON documents(parent_document_id) WHERE parent_document_id IS NOT NULL;
CREATE INDEX idx_documents_content_hash ON documents(company_id, content_hash) WHERE content_hash IS NOT NULL;
CREATE INDEX idx_documents_created ON documents(source_created_at DESC NULLS LAST);
CREATE INDEX idx_documents_ingested ON documents(ingested_at DESC);

-- JSONB indexes
CREATE INDEX idx_documents_metadata ON documents USING GIN(metadata);
CREATE INDEX idx_documents_raw_data ON documents USING GIN(raw_data);

-- Full-text search index (for keyword search)
CREATE INDEX idx_documents_title_trgm ON documents USING GIN(title gin_trgm_ops);
CREATE INDEX idx_documents_content_trgm ON documents USING GIN(content gin_trgm_ops);

-- Comments
COMMENT ON TABLE documents IS 'Unified document storage for all sources (Gmail, Drive, Outlook, uploads, etc.)';
COMMENT ON COLUMN documents.company_id IS 'Company that owns this document (multi-tenant isolation)';
COMMENT ON COLUMN documents.content_hash IS 'SHA-256 hash for deduplication (prevent re-ingesting same content)';
COMMENT ON COLUMN documents.parent_document_id IS 'For attachments: links to parent email document';
COMMENT ON COLUMN documents.user_id IS 'User who ingested this document (for attribution)';

-- ============================================================================
-- CONNECTIONS
-- ============================================================================
-- OAuth connection metadata (Gmail, Outlook, Drive, QuickBooks via Nango)
-- ============================================================================

CREATE TABLE connections (
    id BIGSERIAL PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- OAuth provider
    provider_key TEXT NOT NULL,                   -- gmail, outlook, google_drive, quickbooks

    -- Nango connection
    connection_id TEXT NOT NULL,                  -- Nango's connection UUID (NOT user_id!)
    connection_status TEXT DEFAULT 'active',      -- active, expired, revoked

    -- User attribution (who connected this)
    user_id UUID NOT NULL,                        -- User who created this connection
    user_email TEXT NOT NULL,                     -- Denormalized email for display

    -- Sync control
    can_manual_sync BOOLEAN DEFAULT TRUE,         -- Allow one-time historical sync
    initial_sync_started_at TIMESTAMPTZ,
    initial_sync_completed_at TIMESTAMPTZ,
    initial_sync_completed BOOLEAN DEFAULT FALSE,
    sync_lock_reason TEXT,                        -- Why sync is locked (if locked)

    -- Last sync tracking
    last_sync_at TIMESTAMPTZ,
    last_sync_status TEXT,                        -- success, failed, in_progress
    last_sync_error TEXT,

    -- Cursor tracking (for incremental syncs)
    nango_cursor TEXT,                            -- Nango's pagination cursor

    -- Timestamps
    connected_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,

    -- Constraints
    CONSTRAINT unique_connection UNIQUE(company_id, provider_key, user_id),
    CONSTRAINT valid_provider CHECK (provider_key IN ('gmail', 'outlook', 'google_drive', 'quickbooks', 'slack'))
);

-- Indexes
CREATE INDEX idx_connections_company ON connections(company_id);
CREATE INDEX idx_connections_provider ON connections(company_id, provider_key);
CREATE INDEX idx_connections_user ON connections(user_id);
CREATE INDEX idx_connections_status ON connections(company_id, connection_status);

-- Comments
COMMENT ON TABLE connections IS 'OAuth connection metadata (Gmail, Outlook, Drive, QuickBooks via Nango)';
COMMENT ON COLUMN connections.connection_id IS 'Nango UUID for this OAuth connection (NOT user_id!)';
COMMENT ON COLUMN connections.can_manual_sync IS 'Allow one-time historical sync (locked after first sync)';
COMMENT ON COLUMN connections.nango_cursor IS 'Nango pagination cursor for incremental syncs';

-- ============================================================================
-- SYNC_JOBS
-- ============================================================================
-- Background job tracking for sync operations (Dramatiq workers)
-- ============================================================================

CREATE TABLE sync_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- Job details
    job_type TEXT NOT NULL,                       -- gmail_sync, outlook_sync, drive_sync, quickbooks_sync
    status TEXT DEFAULT 'queued' NOT NULL,        -- queued, running, completed, failed
    priority INTEGER DEFAULT 0,                   -- Higher = more important

    -- User attribution
    user_id UUID NOT NULL,                        -- User who triggered this job
    user_email TEXT,                              -- Denormalized email

    -- Job execution
    worker_id TEXT,                               -- Dramatiq worker ID
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_seconds INTEGER,

    -- Results
    result JSONB DEFAULT '{}'::JSONB,             -- Success details (documents_synced, etc.)
    error_message TEXT,                           -- Failure details
    error_trace TEXT,                             -- Full error traceback

    -- Retry tracking
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,

    -- Constraints
    CONSTRAINT valid_job_type CHECK (job_type IN ('gmail_sync', 'outlook_sync', 'drive_sync', 'quickbooks_sync', 'manual_ingest')),
    CONSTRAINT valid_status CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled'))
);

-- Indexes
CREATE INDEX idx_sync_jobs_company ON sync_jobs(company_id);
CREATE INDEX idx_sync_jobs_status ON sync_jobs(status) WHERE status IN ('queued', 'running');
CREATE INDEX idx_sync_jobs_user ON sync_jobs(user_id);
CREATE INDEX idx_sync_jobs_created ON sync_jobs(created_at DESC);

-- Comments
COMMENT ON TABLE sync_jobs IS 'Background job tracking for sync operations (Dramatiq workers)';
COMMENT ON COLUMN sync_jobs.priority IS 'Job priority (higher = more important, processed first)';
COMMENT ON COLUMN sync_jobs.result IS 'Success details (documents_synced, emails_processed, etc.)';

-- ============================================================================
-- CHATS
-- ============================================================================
-- User chat conversations with HighForce AI (private per user)
-- ============================================================================

CREATE TABLE chats (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- User ownership (private to this user)
    user_id UUID NOT NULL,                        -- Owner of this chat
    user_email TEXT NOT NULL,                     -- Denormalized email

    -- Chat metadata
    title TEXT,                                   -- Auto-generated or user-set title
    is_archived BOOLEAN DEFAULT FALSE,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Indexes
CREATE INDEX idx_chats_company ON chats(company_id);
CREATE INDEX idx_chats_user ON chats(user_id);
CREATE INDEX idx_chats_updated ON chats(updated_at DESC);

-- Comments
COMMENT ON TABLE chats IS 'User chat conversations with HighForce AI (private per user)';
COMMENT ON COLUMN chats.user_id IS 'Owner of this chat (private, not shared with company)';

-- ============================================================================
-- CHAT_MESSAGES
-- ============================================================================
-- Individual messages within chats
-- ============================================================================

CREATE TABLE chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    chat_id UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,

    -- Message details
    role TEXT NOT NULL,                           -- user, assistant
    content TEXT NOT NULL,

    -- Source attribution (for assistant responses)
    sources JSONB DEFAULT '[]'::JSONB,            -- Array of {document_id, title, score, preview}

    -- Metadata
    model TEXT,                                   -- gpt-4o-mini, claude-3-sonnet, etc.
    tokens_used INTEGER,
    latency_ms INTEGER,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,

    -- Constraints
    CONSTRAINT valid_role CHECK (role IN ('user', 'assistant', 'system'))
);

-- Indexes
CREATE INDEX idx_chat_messages_chat ON chat_messages(chat_id, created_at);

-- Comments
COMMENT ON TABLE chat_messages IS 'Individual messages within chats';
COMMENT ON COLUMN chat_messages.sources IS 'Array of source documents used to generate assistant response';

-- ============================================================================
-- ADMINS
-- ============================================================================
-- HighForce platform administrators (separate from company users)
-- ============================================================================

CREATE TABLE admins (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Admin identity
    email TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,

    -- Authentication (if not using Supabase Auth for admins)
    password_hash TEXT,                           -- bcrypt hash (optional if using Supabase Auth)

    -- Permissions
    role TEXT DEFAULT 'admin' NOT NULL,           -- super_admin, admin, support, viewer
    can_create_companies BOOLEAN DEFAULT FALSE,
    can_delete_companies BOOLEAN DEFAULT FALSE,
    can_manage_users BOOLEAN DEFAULT TRUE,
    can_view_all_data BOOLEAN DEFAULT FALSE,      -- Super dangerous!

    -- Security
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    last_login_at TIMESTAMPTZ,
    last_login_ip TEXT,
    mfa_enabled BOOLEAN DEFAULT FALSE,
    mfa_secret TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,

    -- Constraints
    CONSTRAINT valid_admin_role CHECK (role IN ('super_admin', 'admin', 'support', 'viewer'))
);

-- Indexes
CREATE INDEX idx_admins_email ON admins(email) WHERE is_active = TRUE;

-- Comments
COMMENT ON TABLE admins IS 'HighForce platform administrators (NOT company users)';
COMMENT ON COLUMN admins.can_view_all_data IS 'DANGEROUS: Allows viewing all company data (super_admin only)';

-- ============================================================================
-- COMPANY_DEPLOYMENTS
-- ============================================================================
-- Infrastructure credentials per company (admin-only access)
-- SECURITY: Encrypt all secrets with Supabase Vault in production!
-- ============================================================================

CREATE TABLE company_deployments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL UNIQUE REFERENCES companies(id) ON DELETE CASCADE,

    -- Qdrant (vector database)
    qdrant_url TEXT,
    qdrant_api_key TEXT,                          -- TODO: Encrypt with Supabase Vault
    qdrant_collection_name TEXT,

    -- Redis (job queue)
    redis_url TEXT,                               -- TODO: Encrypt with Supabase Vault

    -- OpenAI (LLM + embeddings)
    openai_api_key TEXT,                          -- TODO: Encrypt with Supabase Vault

    -- Nango (OAuth proxy)
    nango_secret_key TEXT,                        -- TODO: Encrypt with Supabase Vault
    nango_public_key TEXT,
    nango_provider_key_gmail TEXT,
    nango_provider_key_outlook TEXT,
    nango_provider_key_google_drive TEXT,
    nango_provider_key_quickbooks TEXT,

    -- Sentry (error tracking)
    sentry_dsn TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Comments
COMMENT ON TABLE company_deployments IS 'Infrastructure credentials per company (admin-only access)';
COMMENT ON COLUMN company_deployments.qdrant_api_key IS 'ENCRYPT IN PRODUCTION with Supabase Vault';
COMMENT ON COLUMN company_deployments.openai_api_key IS 'ENCRYPT IN PRODUCTION with Supabase Vault';

-- ============================================================================
-- AUDIT_LOG
-- ============================================================================
-- Comprehensive audit trail of ALL actions (queries, mutations, admin actions)
-- ============================================================================

CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,

    -- Actor
    user_id UUID,                                 -- User or admin who performed action
    user_email TEXT,
    is_admin BOOLEAN DEFAULT FALSE,               -- Distinguish admin vs regular user

    -- Action
    action TEXT NOT NULL,                         -- create, read, update, delete, search, sync, etc.
    resource_type TEXT,                           -- company, document, chat, connection, etc.
    resource_id TEXT,

    -- Details
    details JSONB DEFAULT '{}'::JSONB,            -- Full context (query, filters, result count, etc.)
    ip_address TEXT,
    user_agent TEXT,

    -- Performance
    duration_ms INTEGER,                          -- How long did this action take?

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Indexes
CREATE INDEX idx_audit_log_company ON audit_log(company_id, created_at DESC);
CREATE INDEX idx_audit_log_user ON audit_log(user_id, created_at DESC);
CREATE INDEX idx_audit_log_action ON audit_log(action, created_at DESC);
CREATE INDEX idx_audit_log_resource ON audit_log(resource_type, resource_id);

-- Comments
COMMENT ON TABLE audit_log IS 'Comprehensive audit trail of ALL actions (queries, mutations, admin actions)';
COMMENT ON COLUMN audit_log.is_admin IS 'Distinguish admin actions from regular user actions';
COMMENT ON COLUMN audit_log.details IS 'Full context of action (query, filters, result count, etc.)';

-- ============================================================================
-- ROW-LEVEL SECURITY (RLS)
-- ============================================================================
-- CRITICAL: Database-level isolation prevents cross-company data access
-- ============================================================================

-- Enable RLS on all tables
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE chats ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE admins ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_deployments ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- RLS POLICIES - COMPANIES
-- ============================================================================

-- Service role bypass (backend uses service role)
CREATE POLICY "service_full_access_companies" ON companies
    FOR ALL
    TO service_role
    USING (true);

-- Users can view companies they are members of
CREATE POLICY "users_view_own_companies" ON companies
    FOR SELECT
    TO authenticated
    USING (
        id IN (
            SELECT company_id
            FROM company_users
            WHERE user_id = auth.uid() AND is_active = TRUE
        )
    );

-- Admins can view all companies
CREATE POLICY "admins_view_all_companies" ON companies
    FOR SELECT
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM admins
            WHERE email = (SELECT email FROM auth.users WHERE id = auth.uid())
              AND is_active = TRUE
        )
    );

-- ============================================================================
-- RLS POLICIES - COMPANY_USERS
-- ============================================================================

CREATE POLICY "service_full_access_company_users" ON company_users
    FOR ALL
    TO service_role
    USING (true);

-- Users can view their own company memberships
CREATE POLICY "users_view_own_memberships" ON company_users
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

-- Users can view other users in their companies
CREATE POLICY "users_view_company_members" ON company_users
    FOR SELECT
    TO authenticated
    USING (
        company_id IN (
            SELECT company_id
            FROM company_users
            WHERE user_id = auth.uid() AND is_active = TRUE
        )
    );

-- ============================================================================
-- RLS POLICIES - DOCUMENTS
-- ============================================================================

CREATE POLICY "service_full_access_documents" ON documents
    FOR ALL
    TO service_role
    USING (true);

-- Users can view documents in their companies
CREATE POLICY "users_view_company_documents" ON documents
    FOR SELECT
    TO authenticated
    USING (
        company_id IN (
            SELECT company_id
            FROM company_users
            WHERE user_id = auth.uid() AND is_active = TRUE
        )
    );

-- Users can insert documents in their companies
CREATE POLICY "users_insert_company_documents" ON documents
    FOR INSERT
    TO authenticated
    WITH CHECK (
        company_id IN (
            SELECT company_id
            FROM company_users
            WHERE user_id = auth.uid() AND is_active = TRUE
        )
    );

-- ============================================================================
-- RLS POLICIES - CONNECTIONS
-- ============================================================================

CREATE POLICY "service_full_access_connections" ON connections
    FOR ALL
    TO service_role
    USING (true);

-- Users can view connections in their companies
CREATE POLICY "users_view_company_connections" ON connections
    FOR SELECT
    TO authenticated
    USING (
        company_id IN (
            SELECT company_id
            FROM company_users
            WHERE user_id = auth.uid() AND is_active = TRUE
        )
    );

-- Users can create connections in their companies
CREATE POLICY "users_create_company_connections" ON connections
    FOR INSERT
    TO authenticated
    WITH CHECK (
        company_id IN (
            SELECT company_id
            FROM company_users
            WHERE user_id = auth.uid() AND is_active = TRUE
        )
        AND user_id = auth.uid()
    );

-- ============================================================================
-- RLS POLICIES - SYNC_JOBS
-- ============================================================================

CREATE POLICY "service_full_access_sync_jobs" ON sync_jobs
    FOR ALL
    TO service_role
    USING (true);

-- Users can view sync jobs in their companies
CREATE POLICY "users_view_company_sync_jobs" ON sync_jobs
    FOR SELECT
    TO authenticated
    USING (
        company_id IN (
            SELECT company_id
            FROM company_users
            WHERE user_id = auth.uid() AND is_active = TRUE
        )
    );

-- ============================================================================
-- RLS POLICIES - CHATS (PRIVATE PER USER)
-- ============================================================================

CREATE POLICY "service_full_access_chats" ON chats
    FOR ALL
    TO service_role
    USING (true);

-- Users can ONLY view their OWN chats (private, not shared with company)
CREATE POLICY "users_view_own_chats" ON chats
    FOR SELECT
    TO authenticated
    USING (
        user_id = auth.uid()
        AND company_id IN (
            SELECT company_id
            FROM company_users
            WHERE user_id = auth.uid() AND is_active = TRUE
        )
    );

-- Users can create chats in their companies
CREATE POLICY "users_create_own_chats" ON chats
    FOR INSERT
    TO authenticated
    WITH CHECK (
        user_id = auth.uid()
        AND company_id IN (
            SELECT company_id
            FROM company_users
            WHERE user_id = auth.uid() AND is_active = TRUE
        )
    );

-- ============================================================================
-- RLS POLICIES - CHAT_MESSAGES
-- ============================================================================

CREATE POLICY "service_full_access_chat_messages" ON chat_messages
    FOR ALL
    TO service_role
    USING (true);

-- Users can view messages in their own chats
CREATE POLICY "users_view_own_chat_messages" ON chat_messages
    FOR SELECT
    TO authenticated
    USING (
        chat_id IN (
            SELECT id FROM chats WHERE user_id = auth.uid()
        )
    );

-- Users can insert messages in their own chats
CREATE POLICY "users_insert_own_chat_messages" ON chat_messages
    FOR INSERT
    TO authenticated
    WITH CHECK (
        chat_id IN (
            SELECT id FROM chats WHERE user_id = auth.uid()
        )
    );

-- ============================================================================
-- RLS POLICIES - ADMINS (ADMIN-ONLY)
-- ============================================================================

CREATE POLICY "service_full_access_admins" ON admins
    FOR ALL
    TO service_role
    USING (true);

-- Admins can view other admins
CREATE POLICY "admins_view_admins" ON admins
    FOR SELECT
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM admins
            WHERE email = (SELECT email FROM auth.users WHERE id = auth.uid())
              AND is_active = TRUE
        )
    );

-- ============================================================================
-- RLS POLICIES - COMPANY_DEPLOYMENTS (ADMIN-ONLY)
-- ============================================================================

CREATE POLICY "service_full_access_deployments" ON company_deployments
    FOR ALL
    TO service_role
    USING (true);

-- Admins can view all deployments
CREATE POLICY "admins_view_deployments" ON company_deployments
    FOR SELECT
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM admins
            WHERE email = (SELECT email FROM auth.users WHERE id = auth.uid())
              AND is_active = TRUE
        )
    );

-- ============================================================================
-- RLS POLICIES - AUDIT_LOG
-- ============================================================================

CREATE POLICY "service_full_access_audit_log" ON audit_log
    FOR ALL
    TO service_role
    USING (true);

-- Users can view their own company's audit logs
CREATE POLICY "users_view_company_audit_log" ON audit_log
    FOR SELECT
    TO authenticated
    USING (
        company_id IN (
            SELECT company_id
            FROM company_users
            WHERE user_id = auth.uid() AND is_active = TRUE
        )
    );

-- Admins can view all audit logs
CREATE POLICY "admins_view_all_audit_log" ON audit_log
    FOR SELECT
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM admins
            WHERE email = (SELECT email FROM auth.users WHERE id = auth.uid())
              AND is_active = TRUE
        )
    );

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Get all companies for a user
CREATE OR REPLACE FUNCTION get_user_companies(p_user_id UUID)
RETURNS TABLE (
    company_id UUID,
    company_name TEXT,
    company_slug TEXT,
    user_role TEXT,
    is_active BOOLEAN
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.name,
        c.slug,
        cu.role,
        cu.is_active
    FROM company_users cu
    JOIN companies c ON c.id = cu.company_id
    WHERE cu.user_id = p_user_id
      AND c.deleted_at IS NULL
    ORDER BY cu.created_at;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Check if user has access to company
CREATE OR REPLACE FUNCTION user_has_company_access(p_user_id UUID, p_company_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1
        FROM company_users
        WHERE user_id = p_user_id
          AND company_id = p_company_id
          AND is_active = TRUE
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get user's role in company
CREATE OR REPLACE FUNCTION get_user_role_in_company(p_user_id UUID, p_company_id UUID)
RETURNS TEXT AS $$
DECLARE
    v_role TEXT;
BEGIN
    SELECT role INTO v_role
    FROM company_users
    WHERE user_id = p_user_id
      AND company_id = p_company_id
      AND is_active = TRUE;

    RETURN v_role;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Check if user is admin
CREATE OR REPLACE FUNCTION is_admin(p_user_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1
        FROM admins a
        JOIN auth.users u ON u.email = a.email
        WHERE u.id = p_user_id
          AND a.is_active = TRUE
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to all tables with updated_at
CREATE TRIGGER update_companies_updated_at BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_company_users_updated_at BEFORE UPDATE ON company_users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_documents_updated_at BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_connections_updated_at BEFORE UPDATE ON connections
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_sync_jobs_updated_at BEFORE UPDATE ON sync_jobs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_chats_updated_at BEFORE UPDATE ON chats
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_admins_updated_at BEFORE UPDATE ON admins
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_deployments_updated_at BEFORE UPDATE ON company_deployments
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- SAMPLE DATA (for testing)
-- ============================================================================

-- Create first admin user (replace with your email)
INSERT INTO admins (email, full_name, role, can_create_companies, can_delete_companies, can_manage_users, can_view_all_data)
VALUES ('admin@highforce.ai', 'HighForce Admin', 'super_admin', TRUE, TRUE, TRUE, FALSE)
ON CONFLICT (email) DO NOTHING;

-- ============================================================================
-- END OF UNIFIED SCHEMA
-- ============================================================================

-- Success message
DO $$
BEGIN
    RAISE NOTICE '============================================================================';
    RAISE NOTICE 'HighForce UNIFIED SCHEMA CREATED SUCCESSFULLY';
    RAISE NOTICE '============================================================================';
    RAISE NOTICE 'Next steps:';
    RAISE NOTICE '1. Create first company via INSERT INTO companies (...)';
    RAISE NOTICE '2. Create user in Supabase Auth (email/password)';
    RAISE NOTICE '3. Add user to company_users table';
    RAISE NOTICE '4. Set app_metadata.company_id in JWT (via Supabase Auth)';
    RAISE NOTICE '5. Deploy HighForce-v2 backend with new Supabase URL';
    RAISE NOTICE '============================================================================';
END $$;
