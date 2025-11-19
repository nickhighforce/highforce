"""
QuickBooks sync engine
Coordinates QuickBooks data sync via Nango unified API

Follows same pattern as Gmail/Outlook sync:
1. Fetch data from Nango
2. Normalize to document format
3. Ingest via ingest_document_universal()
4. Store in Supabase documents table
5. Extract entities into knowledge graph

Each QB record (invoice, bill, payment, customer) becomes a document.
"""
import logging
from typing import Any, Dict, Optional
from datetime import datetime

import httpx
from supabase import Client

from app.services.rag import UniversalIngestionPipeline
from app.services.sync.database import get_connection
from app.services.preprocessing.normalizer import ingest_document_universal
from app.services.sync.providers.quickbooks import nango_fetch_quickbooks_records

logger = logging.getLogger(__name__)


def normalize_quickbooks_invoice(invoice: Dict[str, Any], company_id: str) -> Dict[str, Any]:
    """
    Normalize QB invoice to document format for ingestion.

    Converts structured QB data → text document with metadata.
    """
    # Extract key fields (Nango normalizes these)
    invoice_id = invoice.get("id")
    doc_number = invoice.get("doc_number") or invoice.get("invoice_number") or f"Invoice-{invoice_id}"
    customer_name = invoice.get("customer_name") or invoice.get("customer", {}).get("name", "Unknown Customer")
    total = float(invoice.get("total", 0) or 0)
    balance = float(invoice.get("balance", 0) or 0)
    date = invoice.get("date") or invoice.get("created_at")
    due_date = invoice.get("due_date")
    status = "Paid" if balance == 0 else "Outstanding"

    # Line items
    line_items = invoice.get("line_items", [])
    items_text = "\n".join([
        f"- {item.get('description', 'Item')}: ${item.get('amount', 0)}"
        for item in line_items
    ]) if line_items else "No line items"

    # Create searchable text content
    content = f"""
Invoice: {doc_number}
Customer: {customer_name}
Date: {date}
Due Date: {due_date or 'Not specified'}
Total Amount: ${total:.2f}
Balance Due: ${balance:.2f}
Status: {status}

Line Items:
{items_text}

Notes: {invoice.get('memo') or invoice.get('notes') or 'None'}
""".strip()

    # Metadata for filtering and display
    metadata = {
        "invoice_id": invoice_id,
        "doc_number": doc_number,
        "customer_name": customer_name,
        "total": total,
        "balance": balance,
        "status": status,
        "date": date,
        "due_date": due_date,
        "source_type": "invoice"
    }

    return {
        "company_id": company_id,
        "source": "quickbooks",
        "source_id": f"invoice-{invoice_id}",
        "document_type": "invoice",
        "title": f"Invoice {doc_number} - {customer_name}",
        "content": content,
        "metadata": metadata,
        "raw_data": invoice,
        "source_created_at": datetime.fromisoformat(date.replace("Z", "+00:00")) if date else None
    }


def normalize_quickbooks_bill(bill: Dict[str, Any], company_id: str) -> Dict[str, Any]:
    """Normalize QB bill to document format."""
    bill_id = bill.get("id")
    doc_number = bill.get("doc_number") or f"Bill-{bill_id}"
    vendor_name = bill.get("vendor_name") or bill.get("vendor", {}).get("name", "Unknown Vendor")
    total = float(bill.get("total", 0) or 0)
    balance = float(bill.get("balance", 0) or 0)
    date = bill.get("date") or bill.get("created_at")
    due_date = bill.get("due_date")
    status = "Paid" if balance == 0 else "Unpaid"

    content = f"""
Bill: {doc_number}
Vendor: {vendor_name}
Date: {date}
Due Date: {due_date or 'Not specified'}
Total Amount: ${total:.2f}
Balance Due: ${balance:.2f}
Status: {status}

Description: {bill.get('memo') or bill.get('notes') or 'None'}
""".strip()

    return {
        "company_id": company_id,
        "source": "quickbooks",
        "source_id": f"bill-{bill_id}",
        "document_type": "bill",
        "title": f"Bill {doc_number} - {vendor_name}",
        "content": content,
        "metadata": {
            "bill_id": bill_id,
            "vendor_name": vendor_name,
            "total": total,
            "balance": balance,
            "status": status,
            "date": date,
            "source_type": "bill"
        },
        "raw_data": bill,
        "source_created_at": datetime.fromisoformat(date.replace("Z", "+00:00")) if date else None
    }


def normalize_quickbooks_payment(payment: Dict[str, Any], company_id: str) -> Dict[str, Any]:
    """Normalize QB payment to document format."""
    payment_id = payment.get("id")
    customer_name = payment.get("customer_name") or payment.get("customer", {}).get("name", "Unknown")
    total = float(payment.get("total", 0) or 0)
    date = payment.get("date") or payment.get("created_at")
    payment_method = payment.get("payment_method") or "Unknown"

    content = f"""
Payment Received
Customer: {customer_name}
Amount: ${total:.2f}
Date: {date}
Payment Method: {payment_method}

Reference: {payment.get('reference_number') or payment.get('transaction_id') or 'None'}
Notes: {payment.get('memo') or 'None'}
""".strip()

    return {
        "company_id": company_id,
        "source": "quickbooks",
        "source_id": f"payment-{payment_id}",
        "document_type": "payment",
        "title": f"Payment from {customer_name} - ${total:.2f}",
        "content": content,
        "metadata": {
            "payment_id": payment_id,
            "customer_name": customer_name,
            "total": total,
            "date": date,
            "payment_method": payment_method,
            "source_type": "payment"
        },
        "raw_data": payment,
        "source_created_at": datetime.fromisoformat(date.replace("Z", "+00:00")) if date else None
    }


def normalize_quickbooks_customer(customer: Dict[str, Any], company_id: str) -> Dict[str, Any]:
    """Normalize QB customer to document format."""
    customer_id = customer.get("id")
    customer_name = customer.get("display_name") or customer.get("name", "Unknown Customer")
    email = customer.get("email")
    phone = customer.get("phone")
    company = customer.get("company_name")
    balance = float(customer.get("balance", 0) or 0)

    content = f"""
Customer: {customer_name}
{f"Company: {company}" if company else ""}
Email: {email or 'Not provided'}
Phone: {phone or 'Not provided'}
Balance: ${balance:.2f}

Billing Address:
{customer.get('billing_address', {}).get('line1', 'Not provided')}

Notes: {customer.get('notes') or 'None'}
""".strip()

    return {
        "company_id": company_id,
        "source": "quickbooks",
        "source_id": f"customer-{customer_id}",
        "document_type": "customer",
        "title": f"Customer: {customer_name}",
        "content": content,
        "metadata": {
            "customer_id": customer_id,
            "customer_name": customer_name,
            "email": email,
            "phone": phone,
            "company": company,
            "balance": balance,
            "source_type": "customer"
        },
        "raw_data": customer,
        "source_created_at": datetime.fromisoformat(customer.get("created_at").replace("Z", "+00:00")) if customer.get("created_at") else None
    }


async def run_quickbooks_sync(
    http_client: httpx.AsyncClient,
    supabase: Client,
    cortex_pipeline: Optional[UniversalIngestionPipeline],
    company_id: str,
    provider_key: str = "quickbooks"
) -> Dict[str, Any]:
    """
    Run full QuickBooks sync for a tenant.

    Fetches ALL QB data types and ingests as documents:
    - Invoices
    - Bills
    - Payments
    - Customers

    Each record becomes a searchable document in Supabase + Knowledge Graph.

    Args:
        http_client: Async HTTP client
        supabase: Supabase client
        cortex_pipeline: RAG pipeline (or None if disabled)
        company_id: User/tenant ID
        provider_key: Nango provider key (default: quickbooks)

    Returns:
        Sync statistics
    """
    logger.info(f"🚀 Starting QuickBooks sync for tenant {company_id}")

    records_synced = 0
    errors = []
    stats = {
        "invoices": 0,
        "bills": 0,
        "payments": 0,
        "customers": 0
    }

    try:
        # Get connection ID
        connection_id = await get_connection(company_id, provider_key)
        if not connection_id:
            error_msg = f"No QuickBooks connection found for tenant {company_id}"
            logger.error(error_msg)
            errors.append(error_msg)
            return {
                "status": "error",
                "company_id": company_id,
                "records_synced": 0,
                "errors": errors
            }

        # ========================================================================
        # 1. SYNC INVOICES
        # ========================================================================
        try:
            logger.info("📄 Fetching invoices from QuickBooks...")
            invoices = await nango_fetch_quickbooks_records(http_client, connection_id, "/invoices", limit=200)

            for invoice in invoices:
                try:
                    # Normalize to document format
                    normalized = normalize_quickbooks_invoice(invoice, company_id)

                    # Ingest via universal pipeline (same as emails!)
                    await ingest_document_universal(
                        supabase=supabase,
                        cortex_pipeline=cortex_pipeline,
                        **normalized
                    )

                    records_synced += 1
                    stats["invoices"] += 1

                except Exception as e:
                    logger.error(f"Failed to ingest invoice {invoice.get('id')}: {e}")
                    errors.append(f"Invoice {invoice.get('id')}: {str(e)}")

            logger.info(f"✅ Synced {stats['invoices']} invoices")

        except Exception as e:
            logger.error(f"Failed to fetch invoices: {e}")
            errors.append(f"Invoices: {str(e)}")

        # ========================================================================
        # 2. SYNC BILLS
        # ========================================================================
        try:
            logger.info("📄 Fetching bills from QuickBooks...")
            bills = await nango_fetch_quickbooks_records(http_client, connection_id, "/bills", limit=200)

            for bill in bills:
                try:
                    normalized = normalize_quickbooks_bill(bill, company_id)
                    await ingest_document_universal(
                        supabase=supabase,
                        cortex_pipeline=cortex_pipeline,
                        **normalized
                    )
                    records_synced += 1
                    stats["bills"] += 1
                except Exception as e:
                    logger.error(f"Failed to ingest bill {bill.get('id')}: {e}")
                    errors.append(f"Bill {bill.get('id')}: {str(e)}")

            logger.info(f"✅ Synced {stats['bills']} bills")

        except Exception as e:
            logger.error(f"Failed to fetch bills: {e}")
            errors.append(f"Bills: {str(e)}")

        # ========================================================================
        # 3. SYNC PAYMENTS
        # ========================================================================
        try:
            logger.info("💰 Fetching payments from QuickBooks...")
            payments = await nango_fetch_quickbooks_records(http_client, connection_id, "/payments", limit=200)

            for payment in payments:
                try:
                    normalized = normalize_quickbooks_payment(payment, company_id)
                    await ingest_document_universal(
                        supabase=supabase,
                        cortex_pipeline=cortex_pipeline,
                        **normalized
                    )
                    records_synced += 1
                    stats["payments"] += 1
                except Exception as e:
                    logger.error(f"Failed to ingest payment {payment.get('id')}: {e}")
                    errors.append(f"Payment {payment.get('id')}: {str(e)}")

            logger.info(f"✅ Synced {stats['payments']} payments")

        except Exception as e:
            logger.error(f"Failed to fetch payments: {e}")
            errors.append(f"Payments: {str(e)}")

        # ========================================================================
        # 4. SYNC CUSTOMERS
        # ========================================================================
        try:
            logger.info("👥 Fetching customers from QuickBooks...")
            customers = await nango_fetch_quickbooks_records(http_client, connection_id, "/customers", limit=200)

            for customer in customers:
                try:
                    normalized = normalize_quickbooks_customer(customer, company_id)
                    await ingest_document_universal(
                        supabase=supabase,
                        cortex_pipeline=cortex_pipeline,
                        **normalized
                    )
                    records_synced += 1
                    stats["customers"] += 1
                except Exception as e:
                    logger.error(f"Failed to ingest customer {customer.get('id')}: {e}")
                    errors.append(f"Customer {customer.get('id')}: {str(e)}")

            logger.info(f"✅ Synced {stats['customers']} customers")

        except Exception as e:
            logger.error(f"Failed to fetch customers: {e}")
            errors.append(f"Customers: {str(e)}")

        # ========================================================================
        # SUMMARY
        # ========================================================================
        logger.info("=" * 80)
        logger.info(f"✅ QuickBooks sync complete for tenant {company_id}")
        logger.info(f"Total records synced: {records_synced}")
        logger.info(f"Invoices: {stats['invoices']}")
        logger.info(f"Bills: {stats['bills']}")
        logger.info(f"Payments: {stats['payments']}")
        logger.info(f"Customers: {stats['customers']}")
        logger.info(f"Errors: {len(errors)}")
        logger.info("=" * 80)

        return {
            "status": "success",
            "company_id": company_id,
            "records_synced": records_synced,
            "stats": stats,
            "errors": errors
        }

    except Exception as e:
        logger.error(f"❌ QuickBooks sync failed for tenant {company_id}: {e}")
        return {
            "status": "error",
            "company_id": company_id,
            "records_synced": records_synced,
            "stats": stats,
            "errors": errors + [str(e)]
        }
