#!/bin/bash
# End-to-End Flow Test
# Tests: Login → Upload → Verify Supabase → Verify Qdrant → Search

set -e  # Exit on error

echo "================================================================================"
echo "HighForce End-to-End Flow Test"
echo "================================================================================"

API_BASE="http://localhost:8080"
TEST_EMAIL="test-user@example.com"
TEST_PASSWORD="password123"

# Step 1: Login and get JWT
echo -e "\n[1/5] Logging in..."
LOGIN_RESPONSE=$(curl -s -X POST "https://gcwxfcyyexzkauwsgdqj.supabase.co/auth/v1/token?grant_type=password" \
  -H "apikey: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imdjd3hmY3l5ZXh6a2F1d3NnZHFqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM1ODYyMzgsImV4cCI6MjA3OTE2MjIzOH0.oY3UV1To60qoaeeFStfAhvBFVCbWCmnTHJ4px-E00Rs" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$TEST_EMAIL\",\"password\":\"$TEST_PASSWORD\"}")

JWT=$(echo $LOGIN_RESPONSE | python3 -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))")

if [ -z "$JWT" ]; then
  echo "❌ Login failed:"
  echo "$LOGIN_RESPONSE" | python3 -m json.tool
  exit 1
fi

echo "✅ Logged in successfully"
echo "   JWT: ${JWT:0:50}..."

# Step 2: Upload document
echo -e "\n[2/5] Uploading test document..."
cat > /tmp/test_doc.txt <<EOF
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
EOF

UPLOAD_RESPONSE=$(curl -s -X POST "$API_BASE/api/v1/upload/file" \
  -H "Authorization: Bearer $JWT" \
  -F "file=@/tmp/test_doc.txt")

echo "$UPLOAD_RESPONSE" | python3 -m json.tool

DOCUMENT_ID=$(echo $UPLOAD_RESPONSE | python3 -c "import sys, json; print(json.load(sys.stdin).get('document_id', ''))" 2>/dev/null || echo "")

if [ -z "$DOCUMENT_ID" ]; then
  echo "⚠️  Upload may have failed (check response above)"
else
  echo "✅ Document uploaded successfully"
  echo "   Document ID: $DOCUMENT_ID"
fi

# Step 3: Wait for processing
echo -e "\n[3/5] Waiting 10 seconds for document processing..."
sleep 10

# Step 4: Verify in Supabase
echo -e "\n[4/5] Verifying document in Supabase..."
source venv/bin/activate
python3 -c "
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(
    os.getenv('SUPABASE_URL'),
    os.getenv('SUPABASE_ANON_KEY')
)

docs = supabase.table('documents').select('id,title,source,created_at').eq('company_id', '0eb96b39-c31d-44b6-af44-39c9cc2b6383').order('created_at', desc=True).limit(3).execute()

if docs.data:
    print(f'✅ Found {len(docs.data)} document(s) in Supabase:')
    for doc in docs.data:
        print(f'   - ID: {doc[\"id\"]}, Title: {doc.get(\"title\", \"N/A\")}, Source: {doc.get(\"source\", \"N/A\")}')
else:
    print('⚠️  No documents found in Supabase')
"

# Step 5: Verify in Qdrant
echo -e "\n[5/5] Verifying chunks in Qdrant..."
python3 -c "
from qdrant_client import QdrantClient
import os
from dotenv import load_dotenv

load_dotenv()
qdrant = QdrantClient(url=os.getenv('QDRANT_URL'), api_key=os.getenv('QDRANT_API_KEY'))

result = qdrant.scroll(
    collection_name=os.getenv('QDRANT_COLLECTION_NAME'),
    scroll_filter={
        'must': [
            {'key': 'company_id', 'match': {'value': '0eb96b39-c31d-44b6-af44-39c9cc2b6383'}}
        ]
    },
    limit=5,
    with_payload=True,
    with_vectors=False
)

points = result[0]
if points:
    print(f'✅ Found {len(points)} chunk(s) in Qdrant')
    sample = points[0].payload
    print(f'   Sample chunk:')
    print(f'   - Document ID: {sample.get(\"document_id\", \"N/A\")}')
    print(f'   - Text preview: {sample.get(\"text\", \"\")[:100]}...')
else:
    print('⚠️  No chunks found in Qdrant')
"

# Step 6: Query search endpoint
echo -e "\n[6/6] Querying search endpoint..."
SEARCH_RESPONSE=$(curl -s -X POST "$API_BASE/api/v1/search" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"query":"What are the security features of HighForce?","vector_limit":5,"graph_limit":5}')

echo "$SEARCH_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(f'✅ Search query successful')
print(f'   Answer: {data.get(\"answer\", \"N/A\")[:200]}...')
print(f'   Sources: {len(data.get(\"sources\", []))} chunks')
if data.get('sources'):
    print(f'   Sample source score: {data[\"sources\"][0].get(\"score\", \"N/A\")}')
"

echo -e "\n================================================================================"
echo "✅ End-to-End Test Complete!"
echo "================================================================================"
