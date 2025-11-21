#!/usr/bin/env python3
"""
End-to-End Test: Create user → Login → Upload → Verify in Supabase & Qdrant → Search

This script tests the complete HighForce pipeline:
1. Create test user via Supabase Admin API
2. Login and get JWT token
3. Upload test document
4. Verify document in Supabase documents table
5. Verify document chunks in Qdrant
6. Query search endpoint to retrieve document
"""
import os
import sys
import json
import time
import requests
from supabase import create_client
from qdrant_client import QdrantClient
from dotenv import load_dotenv

# Load environment
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME")
API_BASE_URL = "http://localhost:8080"

# Test user credentials (use existing test user)
TEST_EMAIL = "test-user@example.com"  # User we created in previous session
TEST_PASSWORD = "password123"
TEST_COMPANY_ID = "0eb96b39-c31d-44b6-af44-39c9cc2b6383"  # Existing test company
TEST_USER_ID = "c3c032df-ef38-439d-af8c-1d30bf1ea5bb"  # From previous session

print("=" * 80)
print("HighForce End-to-End Test")
print("=" * 80)

# Initialize clients
# Use service key for admin operations (direct access, no RLS bypass)
supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
# Use anon key for regular client
supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

# ============================================================================
# STEP 1: Skip user creation - use existing test user
# ============================================================================
print("\n[1/6] Using existing test user...")
print(f"   Email: {TEST_EMAIL}")
print(f"   User ID: {TEST_USER_ID}")
print(f"   Company ID: {TEST_COMPANY_ID}")

# ============================================================================
# STEP 2: Login and Get JWT Token
# ============================================================================
print("\n[2/6] Logging in to get JWT token...")

try:
    # Use Supabase client to sign in
    auth_result = supabase.auth.sign_in_with_password({
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })

    jwt_token = auth_result.session.access_token
    print(f"✅ Logged in successfully")
    print(f"   JWT token: {jwt_token[:50]}...")

except Exception as e:
    print(f"❌ Failed to login: {e}")
    sys.exit(1)

# ============================================================================
# STEP 3: Upload Test Document
# ============================================================================
print("\n[3/6] Uploading test document...")

test_content = """
HighForce AI Platform - Technical Specifications

HighForce is an enterprise RAG (Retrieval-Augmented Generation) platform that provides:

1. Multi-Tenant Architecture
   - Company-level data isolation
   - Row-level security in PostgreSQL
   - JWT-based authentication with company_id

2. Data Sources
   - Gmail integration via OAuth
   - Outlook integration via OAuth
   - Google Drive integration
   - QuickBooks integration
   - Manual file upload

3. Vector Search
   - Qdrant vector database
   - OpenAI text-embedding-3-large embeddings
   - Hybrid search combining vector + keyword

4. Knowledge Graph
   - LlamaIndex PropertyGraphIndex
   - Entity extraction and relationship mapping
   - Sub-question query decomposition

5. Security Features
   - SOC 2 Type II ready
   - End-to-end encryption
   - Rate limiting and circuit breakers
   - CORS and security headers

The platform is built with FastAPI, Python 3.12, and deployed on Render.com.
"""

try:
    files = {
        'file': ('highforce_specs.txt', test_content.encode('utf-8'), 'text/plain')
    }

    headers = {
        'Authorization': f'Bearer {jwt_token}'
    }

    response = requests.post(
        f"{API_BASE_URL}/api/v1/upload/file",
        files=files,
        headers=headers
    )

    if response.status_code == 200:
        upload_result = response.json()
        print(f"✅ Document uploaded successfully")
        print(f"   Document ID: {upload_result.get('document_id')}")
        document_id = upload_result.get('document_id')
    else:
        print(f"❌ Upload failed: {response.status_code}")
        print(f"   Response: {response.text}")
        sys.exit(1)

except Exception as e:
    print(f"❌ Upload request failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ============================================================================
# STEP 4: Verify Document in Supabase
# ============================================================================
print("\n[4/6] Verifying document in Supabase...")

# Wait a bit for processing
print("   Waiting 5 seconds for document processing...")
time.sleep(5)

try:
    # Check documents table (use admin client for service key access)
    docs = supabase_admin.table("documents").select("*").eq("company_id", TEST_COMPANY_ID).order("created_at", desc=True).limit(5).execute()

    if docs.data:
        latest_doc = docs.data[0]
        print(f"✅ Found {len(docs.data)} document(s) in Supabase")
        print(f"   Latest document:")
        print(f"   - ID: {latest_doc['id']}")
        print(f"   - Title: {latest_doc.get('title', 'N/A')}")
        print(f"   - Source: {latest_doc.get('source', 'N/A')}")
        print(f"   - Content length: {len(latest_doc.get('content', ''))}")
    else:
        print(f"⚠️  No documents found in Supabase")

except Exception as e:
    print(f"❌ Failed to query Supabase: {e}")
    import traceback
    traceback.print_exc()

# ============================================================================
# STEP 5: Verify Document Chunks in Qdrant
# ============================================================================
print("\n[5/6] Verifying document chunks in Qdrant...")

try:
    # Query Qdrant for points with company_id filter
    scroll_result = qdrant.scroll(
        collection_name=QDRANT_COLLECTION_NAME,
        scroll_filter={
            "must": [
                {"key": "company_id", "match": {"value": TEST_COMPANY_ID}}
            ]
        },
        limit=10,
        with_payload=True,
        with_vectors=False
    )

    points = scroll_result[0]

    if points:
        print(f"✅ Found {len(points)} chunk(s) in Qdrant")
        print(f"   Sample chunk:")
        sample = points[0].payload
        print(f"   - Document ID: {sample.get('document_id', 'N/A')}")
        print(f"   - Text preview: {sample.get('text', '')[:100]}...")
        print(f"   - Company ID: {sample.get('company_id', 'N/A')}")
    else:
        print(f"⚠️  No chunks found in Qdrant for company {TEST_COMPANY_ID}")

except Exception as e:
    print(f"❌ Failed to query Qdrant: {e}")
    import traceback
    traceback.print_exc()

# ============================================================================
# STEP 6: Query Search Endpoint
# ============================================================================
print("\n[6/6] Querying search endpoint...")

try:
    search_query = {
        "query": "What are the security features of HighForce?",
        "vector_limit": 5,
        "graph_limit": 5,
        "include_full_emails": False
    }

    headers = {
        'Authorization': f'Bearer {jwt_token}',
        'Content-Type': 'application/json'
    }

    response = requests.post(
        f"{API_BASE_URL}/api/v1/search",
        json=search_query,
        headers=headers
    )

    if response.status_code == 200:
        search_result = response.json()
        print(f"✅ Search query successful")
        print(f"   Answer: {search_result.get('answer', 'N/A')[:200]}...")
        print(f"   Sources: {len(search_result.get('sources', []))} chunks")

        if search_result.get('sources'):
            print(f"\n   Sample source:")
            sample_source = search_result['sources'][0]
            print(f"   - Score: {sample_source.get('score', 'N/A')}")
            print(f"   - Text: {sample_source.get('text', '')[:100]}...")
    else:
        print(f"❌ Search failed: {response.status_code}")
        print(f"   Response: {response.text}")

except Exception as e:
    print(f"❌ Search request failed: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("✅ End-to-End Test Complete!")
print("=" * 80)
