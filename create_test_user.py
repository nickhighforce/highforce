#!/usr/bin/env python3
from supabase import create_client
import os

# Hardcoded credentials (from .env)
SUPABASE_URL = "https://gcwxfcyyexzkauwsgdqj.supabase.co"
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imdjd3hmY3l5ZXh6a2F1d3NnZHFqIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MzU4NjIzOCwiZXhwIjoyMDc5MTYyMjM4fQ.cqT1xnriDJ7ZFm69AoT5SXFBZbJ-9OJocLT8h_coE1E"

# Test user
TEST_EMAIL = "test-user@example.com"
TEST_PASSWORD = "password123"
TEST_COMPANY_ID = "0eb96b39-c31d-44b6-af44-39c9cc2b6383"

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

try:
    # Create auth user
    auth_result = supabase.auth.admin.create_user({
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
        "email_confirm": True,
        "user_metadata": {},
        "app_metadata": {"company_id": TEST_COMPANY_ID}
    })

    user_id = auth_result.user.id
    print(f"✅ Created auth user: {user_id}")

    # Link to company
    supabase.table("company_users").insert({
        "user_id": user_id,
        "company_id": TEST_COMPANY_ID,
        "email": TEST_EMAIL,
        "role": "admin",
        "is_active": True
    }).execute()

    print(f"✅ Linked user to company")
    print(f"   Email: {TEST_EMAIL}")
    print(f"   Password: {TEST_PASSWORD}")

except Exception as e:
    if "already" in str(e).lower():
        print(f"✅ User {TEST_EMAIL} already exists")
    else:
        print(f"❌ Error: {e}")
        raise
