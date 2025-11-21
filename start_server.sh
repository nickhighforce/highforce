#!/bin/bash
# HighForce v1 Local Development Server Startup
# Run this script in a NEW terminal to avoid environment variable conflicts

cd "$(dirname "$0")"

# Kill any existing servers
echo "🔴 Stopping existing servers..."
lsof -ti:8080 | xargs kill -9 2>/dev/null
pkill -9 -f "uvicorn main:app" 2>/dev/null
sleep 2

# Unset old CORTEX environment variables that may conflict (but keep Supabase for auth!)
# unset SUPABASE_URL SUPABASE_ANON_KEY SUPABASE_SERVICE_KEY
unset MASTER_SUPABASE_URL MASTER_SUPABASE_SERVICE_KEY
# unset QDRANT_URL QDRANT_API_KEY QDRANT_COLLECTION_NAME
unset DATABASE_URL TEST_CONNECTION_ID

echo "✅ Environment cleaned"

# Activate venv
source venv/bin/activate

echo "🚀 Starting HighForce v1 server..."
echo "📍 Using .env file for configuration"
echo "🌐 Server will be available at http://localhost:8080"
echo ""

# Start server (will read from .env file)
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
