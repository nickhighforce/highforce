# HighForce - Enterprise RAG Platform
**Version:** 1.0.0
**Architecture:** Unified Multi-Tenant SaaS
**Security:** SOC 2 Type II Ready
**License:** Proprietary - ThunderbirdLabs

---

## 🎯 What is HighForce?

Enterprise-grade Retrieval-Augmented Generation (RAG) platform that transforms scattered business data into an AI-powered intelligence system.

**Core Value Proposition:**
- Connect Gmail, Outlook, Drive, QuickBooks via OAuth
- Automatic data ingestion with spam filtering
- Hybrid search (vector + keyword + knowledge graph)
- Natural language Q&A with source citations
- Private per-user chats + shared company data
- SOC 2 compliant multi-tenant architecture

---

## 🏗️ Architecture

### Industry Standard Design
Same architecture as **Slack, Notion, Linear, Vercel, GitHub**:
- ✅ Single unified database (ONE Supabase)
- ✅ Row-Level Security (RLS) for database-level isolation
- ✅ JWT authentication with custom claims (company_id in metadata)
- ✅ Uniform naming conventions (company_id everywhere)
- ✅ Service-oriented architecture (clean separation of concerns)

### Technology Stack
```
Backend:      FastAPI + Python 3.12
Database:     Supabase (PostgreSQL + Auth + Storage)
Vector DB:    Qdrant (semantic search)
Job Queue:    Redis + Dramatiq (background workers)
AI/ML:        OpenAI (GPT-4o-mini + text-embedding-3-small)
OAuth:        Nango (unified OAuth proxy)
Monitoring:   Sentry (error tracking)
```

### Deployment
```
Production:   Render.com (Docker containers)
Frontend:     Vercel (Next.js)
Admin:        Separate admin portal (admin.highforce.ai)
```

---

## 📁 Project Structure

```
HighForce-v1/
├── main.py                         # FastAPI app entry point
├── worker.py                       # Dramatiq background worker
├── requirements.txt                # Python dependencies (pinned versions)
├── Dockerfile                      # Production container
├── render-build.sh                 # Render deployment script
├── .env.example                    # Environment variable template
│
├── migrations/
│   └── 001_unified_schema.sql     # Complete database schema with RLS
│
├── docs/
│   ├── ARCHITECTURE.md            # System architecture
│   ├── API.md                     # API documentation
│   ├── SECURITY.md                # Security model (SOC 2)
│   └── DEPLOYMENT.md              # Deployment guide
│
├── tests/
│   ├── unit/                      # Unit tests
│   ├── integration/               # Integration tests
│   └── conftest.py                # Pytest configuration
│
└── app/
    ├── core/                       # Configuration & dependencies
    │   ├── config.py              # Unified settings (ONE Supabase)
    │   ├── security.py            # JWT validation + RLS
    │   ├── dependencies.py        # Dependency injection
    │   └── validation.py          # Input validation
    │
    ├── middleware/                 # Request processing
    │   ├── cors.py                # CORS configuration
    │   ├── error_handler.py       # Global error handling
    │   ├── logging.py             # Request/response logging
    │   ├── rate_limit.py          # Rate limiting (SlowAPI)
    │   ├── security_headers.py    # OWASP security headers
    │   └── audit.py               # Comprehensive audit logging (NEW)
    │
    ├── models/schemas/             # Pydantic schemas
    │   ├── company.py             # Company models
    │   ├── user.py                # User models
    │   ├── document.py            # Document models
    │   ├── connection.py          # OAuth connection models
    │   ├── sync.py                # Sync job models
    │   ├── chat.py                # Chat models
    │   └── search.py              # Search models
    │
    ├── services/                   # Business logic
    │   ├── oauth/                 # OAuth connection management
    │   │   ├── nango_client.py   # Nango API client
    │   │   └── connections.py    # Connection CRUD
    │   │
    │   ├── sync/                  # Data synchronization
    │   │   ├── orchestration/    # Sync orchestrators
    │   │   │   ├── email_sync.py
    │   │   │   ├── drive_sync.py
    │   │   │   └── quickbooks_sync.py
    │   │   ├── providers/        # Provider-specific clients
    │   │   │   ├── gmail.py
    │   │   │   ├── outlook.py
    │   │   │   ├── google_drive.py
    │   │   │   └── quickbooks.py
    │   │   ├── database.py       # Save synced data to Supabase
    │   │   └── persistence.py    # Document persistence helpers
    │   │
    │   ├── ingestion/             # Document processing
    │   │   ├── parser.py         # File parsing (PDF, DOCX, images)
    │   │   ├── ocr.py            # OCR with Tesseract
    │   │   ├── chunker.py        # Text chunking (1000 chars)
    │   │   ├── embedder.py       # OpenAI embeddings
    │   │   ├── spam_filter.py    # OpenAI spam classifier
    │   │   └── deduplicator.py   # SHA-256 content deduplication
    │   │
    │   ├── rag/                   # Retrieval-Augmented Generation
    │   │   ├── indexer.py        # Qdrant indexing
    │   │   ├── query.py          # Hybrid search engine
    │   │   ├── reranker.py       # Cross-encoder reranking
    │   │   └── recency.py        # Recency boost postprocessor
    │   │
    │   ├── reporting/             # Intelligence & reports
    │   │   ├── generator.py      # Report generation
    │   │   ├── insights.py       # RAG-powered insights
    │   │   └── alerts.py         # Real-time document alerts
    │   │
    │   └── jobs/                  # Background workers
    │       ├── broker.py         # Dramatiq + Redis config
    │       └── tasks.py          # Sync tasks (gmail, outlook, drive)
    │
    └── api/v1/routes/             # API endpoints
        ├── health.py              # Health check
        ├── oauth.py               # OAuth connection flow
        ├── webhook.py             # Nango webhooks
        ├── sync.py                # Manual sync triggers
        ├── search.py              # Hybrid search endpoint
        ├── chat.py                # Chat interface
        ├── upload.py              # File upload + ingestion
        ├── reports.py             # Reports & insights API
        ├── admin.py               # Admin control plane
        └── users.py               # User management + invitations
```

---

## 🔐 Security Model (SOC 2 Ready)

### Multi-Layer Defense
1. **JWT Authentication** - Supabase Auth with custom company_id claim
2. **Row-Level Security (RLS)** - Database enforces isolation (even if backend compromised)
3. **API Rate Limiting** - SlowAPI prevents abuse (100 req/hour per endpoint)
4. **OWASP Security Headers** - HSTS, CSP, X-Frame-Options, etc.
5. **Comprehensive Audit Logging** - All queries tracked (who, what, when, IP)
6. **Encrypted Secrets** - Supabase Vault for OAuth tokens (production)

### Data Isolation
```sql
-- Example RLS policy (prevents cross-company access)
CREATE POLICY "users_view_company_documents" ON documents
    FOR SELECT TO authenticated
    USING (
        company_id IN (
            SELECT company_id FROM company_users
            WHERE user_id = auth.uid() AND is_active = TRUE
        )
    );
```

**Result:** User A cannot query User B's data, even with SQL injection or compromised backend!

---

## 🚀 Quick Start

### Prerequisites
- Python 3.12+
- PostgreSQL (via Supabase)
- Qdrant Cloud account
- Redis instance (Upstash or Redis Cloud)
- OpenAI API key
- Nango account (for OAuth)

### 1. Environment Setup
```bash
cp .env.example .env
# Fill in credentials:
# - SUPABASE_URL, SUPABASE_SERVICE_KEY
# - QDRANT_URL, QDRANT_API_KEY
# - REDIS_URL
# - OPENAI_API_KEY
# - NANGO_SECRET_KEY
```

### 2. Database Migration
```bash
# Run unified schema on your Supabase project
psql $DATABASE_URL < migrations/001_unified_schema.sql
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run Development Server
```bash
uvicorn main:app --reload --port 8080
```

### 5. Run Background Worker
```bash
# In separate terminal
python worker.py
```

### 6. Test API
```bash
curl http://localhost:8080/health
# Returns: {"status": "healthy", "version": "1.0.0"}
```

---

## 📡 API Endpoints

### Core Features
```
GET  /health                           # Health check
POST /api/v1/oauth/connect/start      # Start OAuth flow (Gmail, Outlook, etc.)
POST /api/v1/oauth/nango/callback     # Nango webhook (connection established)
POST /api/v1/sync/initial/{provider}  # Trigger historical sync (1 year)
POST /api/v1/search                    # Hybrid search (vector + keyword)
POST /api/v1/chat                      # Chat with context retention
POST /api/v1/upload                    # Upload files (PDF, DOCX, images)
GET  /api/v1/reports                   # Generate intelligence reports
```

### Admin
```
GET    /api/v1/admin/companies        # List all companies
POST   /api/v1/admin/companies        # Create new company
PATCH  /api/v1/admin/companies/:id    # Update company settings
GET    /api/v1/admin/sync-monitoring  # Monitor sync jobs across all companies
```

### User Management
```
POST /api/v1/users/invite             # Invite user to company
GET  /api/v1/users/team               # List team members
```

---

## 🧪 Testing

### Run Tests
```bash
# Unit tests
pytest tests/unit -v

# Integration tests (requires running backend)
pytest tests/integration -v

# All tests with coverage
pytest --cov=app --cov-report=html
```

### Example Test
```python
def test_search_requires_auth(client):
    """Search endpoint requires authentication"""
    response = client.post("/api/v1/search", json={"query": "test"})
    assert response.status_code == 401
```

---

## 📊 Monitoring & Observability

### Sentry Error Tracking
```python
# Automatic error capture
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1
    )
```

### Audit Logging
All actions logged to `audit_log` table:
```sql
SELECT
    user_email,
    action,
    resource_type,
    created_at
FROM audit_log
WHERE company_id = '<company_id>'
ORDER BY created_at DESC
LIMIT 100;
```

### Performance Metrics
- Search latency: <100ms (95th percentile)
- Chat response: <2s (with sources)
- OAuth connection: <5s
- Initial sync: <30min (1 year Gmail)

---

## 🌐 Deployment

### Render.com (Production)
```bash
# render-build.sh runs automatically on deploy
# 1. Installs dependencies
# 2. Pre-downloads AI models (reranker)
# 3. Sets up Google Cloud credentials (for OCR)
```

### Environment Variables (Production)
```bash
# Supabase (unified database)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your_service_key

# Qdrant (vector search)
QDRANT_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=your_api_key
QDRANT_COLLECTION_NAME=company_documents

# Redis (job queue)
REDIS_URL=redis://default:password@host:port

# OpenAI
OPENAI_API_KEY=sk-...

# Nango (OAuth)
NANGO_SECRET_KEY=your_secret_key

# Sentry (error tracking)
SENTRY_DSN=https://...@sentry.io/...

# Environment
ENVIRONMENT=production
DEBUG=false
```

---

## 🤝 Contributing

### Code Standards
- **PEP 8** - Python style guide
- **Type hints** - All functions annotated
- **Docstrings** - Google style
- **Tests** - >80% coverage required
- **Security** - No secrets in code (use environment variables)

### Naming Conventions
- `company_id` - Always use (never `tenant_id`)
- `user_id` - Individual user identifier
- `connection_id` - Nango OAuth connection UUID
- Snake case for Python (functions, variables)
- PascalCase for classes (models, schemas)

---

## 📄 License

Proprietary - ThunderbirdLabs / HighForce
© 2025 All Rights Reserved

---

## 📞 Support

- **Documentation:** https://docs.highforce.ai
- **Support:** support@highforce.ai
- **Sales:** sales@highforce.ai
- **Status:** https://status.highforce.ai

---

**Built with ❤️ by the ThunderbirdLabs team**
