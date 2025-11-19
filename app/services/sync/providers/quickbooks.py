"""
QuickBooks Integration via Nango Unified API
Fetches financial data, invoices, customers, vendors, and more

Uses Nango's pre-synced QuickBooks data (no direct QB API calls needed!)
Nango syncs data automatically in the background.
"""

import logging
from typing import Dict, Any, Optional, List
import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


async def nango_fetch_quickbooks_records(
    http_client: httpx.AsyncClient,
    connection_id: str,
    endpoint: str,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Fetch QuickBooks records from Nango unified API.

    Nango syncs QB data in the background, so this is FAST!

    Args:
        http_client: Async HTTP client
        connection_id: Nango connection ID (user ID)
        endpoint: Nango endpoint (e.g., "/invoices", "/customers", "/bills")
        limit: Max records to fetch

    Returns:
        List of records

    Example:
        invoices = await nango_fetch_quickbooks_records(
            http_client,
            "user-123",
            "/invoices"
        )
    """
    url = f"https://api.nango.dev/quickbooks{endpoint}"

    headers = {
        "Authorization": f"Bearer {settings.nango_secret}",
        "Connection-Id": connection_id,
        "Provider-Config-Key": "quickbooks"
    }

    params = {"limit": limit}

    logger.info(f"📊 Fetching QuickBooks {endpoint} (limit={limit})")

    try:
        response = await http_client.get(url, headers=headers, params=params, timeout=30.0)
        response.raise_for_status()

        data = response.json()
        records = data.get("data", [])  # Nango wraps response in { "data": [...] }

        logger.info(f"✅ Fetched {len(records)} QuickBooks records from {endpoint}")
        return records

    except httpx.HTTPStatusError as e:
        logger.error(f"❌ QuickBooks API error: {e.response.status_code} - {e.response.text[:500]}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"QuickBooks API error: {e.response.text[:200]}"
        )
    except Exception as e:
        logger.error(f"❌ QuickBooks fetch error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def fetch_all_quickbooks_data(
    http_client: httpx.AsyncClient,
    connection_id: str
) -> Dict[str, Any]:
    """
    Fetch ALL QuickBooks data for CEO dashboard.

    Calls all available Nango QuickBooks endpoints in parallel.

    Args:
        http_client: Async HTTP client
        connection_id: Nango connection ID (user ID)

    Returns:
        Dictionary with all fetched data:
        {
            "accounts": [...],
            "invoices": [...],
            "customers": [...],
            "vendors": [...],
            "bills": [...],
            "payments": [...],
            "items": [...],
            "purchases": [...],
            "deposits": [...],
            "transfers": [...],
            "bill_payments": [...],
            "credit_memos": [...],
            "journal_entries": [...]
        }
    """
    logger.info(f"🔍 Fetching ALL QuickBooks data for connection {connection_id}")

    results = {}

    # ============================================================================
    # CORE FINANCIAL DATA
    # ============================================================================

    # Accounts (Chart of Accounts)
    try:
        accounts = await nango_fetch_quickbooks_records(http_client, connection_id, "/accounts", limit=500)
        results["accounts"] = accounts
        logger.info(f"✅ Accounts: {len(accounts)} found")
    except Exception as e:
        logger.error(f"Failed to fetch accounts: {e}")
        results["accounts"] = []

    # Invoices (sales to customers)
    try:
        invoices = await nango_fetch_quickbooks_records(http_client, connection_id, "/invoices", limit=200)
        results["invoices"] = invoices
        logger.info(f"✅ Invoices: {len(invoices)} found")
    except Exception as e:
        logger.error(f"Failed to fetch invoices: {e}")
        results["invoices"] = []

    # Customers
    try:
        customers = await nango_fetch_quickbooks_records(http_client, connection_id, "/customers", limit=200)
        results["customers"] = customers
        logger.info(f"✅ Customers: {len(customers)} found")
    except Exception as e:
        logger.error(f"Failed to fetch customers: {e}")
        results["customers"] = []

    # Bills (purchases from vendors)
    try:
        bills = await nango_fetch_quickbooks_records(http_client, connection_id, "/bills", limit=200)
        results["bills"] = bills
        logger.info(f"✅ Bills: {len(bills)} found")
    except Exception as e:
        logger.error(f"Failed to fetch bills: {e}")
        results["bills"] = []

    # Bill Payments
    try:
        bill_payments = await nango_fetch_quickbooks_records(http_client, connection_id, "/bill-payments", limit=200)
        results["bill_payments"] = bill_payments
        logger.info(f"✅ Bill Payments: {len(bill_payments)} found")
    except Exception as e:
        logger.error(f"Failed to fetch bill payments: {e}")
        results["bill_payments"] = []

    # Payments Received
    try:
        payments = await nango_fetch_quickbooks_records(http_client, connection_id, "/payments", limit=200)
        results["payments"] = payments
        logger.info(f"✅ Payments: {len(payments)} found")
    except Exception as e:
        logger.error(f"Failed to fetch payments: {e}")
        results["payments"] = []

    # Items (products/services sold)
    try:
        items = await nango_fetch_quickbooks_records(http_client, connection_id, "/items", limit=200)
        results["items"] = items
        logger.info(f"✅ Items: {len(items)} found")
    except Exception as e:
        logger.error(f"Failed to fetch items: {e}")
        results["items"] = []

    # Purchases
    try:
        purchases = await nango_fetch_quickbooks_records(http_client, connection_id, "/purchases", limit=200)
        results["purchases"] = purchases
        logger.info(f"✅ Purchases: {len(purchases)} found")
    except Exception as e:
        logger.error(f"Failed to fetch purchases: {e}")
        results["purchases"] = []

    # Deposits
    try:
        deposits = await nango_fetch_quickbooks_records(http_client, connection_id, "/deposits", limit=200)
        results["deposits"] = deposits
        logger.info(f"✅ Deposits: {len(deposits)} found")
    except Exception as e:
        logger.error(f"Failed to fetch deposits: {e}")
        results["deposits"] = []

    # Transfers
    try:
        transfers = await nango_fetch_quickbooks_records(http_client, connection_id, "/transfers", limit=200)
        results["transfers"] = transfers
        logger.info(f"✅ Transfers: {len(transfers)} found")
    except Exception as e:
        logger.error(f"Failed to fetch transfers: {e}")
        results["transfers"] = []

    # Credit Memos
    try:
        credit_memos = await nango_fetch_quickbooks_records(http_client, connection_id, "/credit-memos", limit=200)
        results["credit_memos"] = credit_memos
        logger.info(f"✅ Credit Memos: {len(credit_memos)} found")
    except Exception as e:
        logger.error(f"Failed to fetch credit memos: {e}")
        results["credit_memos"] = []

    # Journal Entries
    try:
        journal_entries = await nango_fetch_quickbooks_records(http_client, connection_id, "/journal-entries", limit=200)
        results["journal_entries"] = journal_entries
        logger.info(f"✅ Journal Entries: {len(journal_entries)} found")
    except Exception as e:
        logger.error(f"Failed to fetch journal entries: {e}")
        results["journal_entries"] = []

    # ============================================================================
    # SUMMARY
    # ============================================================================
    logger.info("=" * 80)
    logger.info("✅ QuickBooks Data Fetch Complete!")
    logger.info(f"Accounts: {len(results.get('accounts', []))}")
    logger.info(f"Invoices: {len(results.get('invoices', []))}")
    logger.info(f"Customers: {len(results.get('customers', []))}")
    logger.info(f"Bills: {len(results.get('bills', []))}")
    logger.info(f"Payments: {len(results.get('payments', []))}")
    logger.info(f"Items: {len(results.get('items', []))}")
    logger.info("=" * 80)

    return results
