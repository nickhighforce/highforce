"""
Validation utilities for user invitations and permissions
"""
import re
from typing import Dict
from fastapi import HTTPException
from supabase import Client


def extract_domain(email: str) -> str:
    """
    Extract domain from email address.

    Args:
        email: Email address (e.g., "user@example.com")

    Returns:
        Domain in lowercase (e.g., "example.com")

    Raises:
        ValueError: If email format is invalid
    """
    if not email or '@' not in email:
        raise ValueError(f"Invalid email format: {email}")

    domain = email.split('@')[1].lower().strip()

    # Basic domain validation
    if not re.match(r'^[a-z0-9][a-z0-9\-\.]*[a-z0-9]$', domain):
        raise ValueError(f"Invalid domain format: {domain}")

    return domain


def validate_invitation_domain(inviter_email: str, invitee_email: str) -> Dict[str, any]:
    """
    Check if invitee email domain matches inviter's domain.
    Returns warning if domains differ.

    Args:
        inviter_email: Email of user sending invitation
        invitee_email: Email of user being invited

    Returns:
        {
            "warning": bool,
            "message": str (only if warning=True),
            "inviter_domain": str,
            "invitee_domain": str
        }
    """
    try:
        inviter_domain = extract_domain(inviter_email)
        invitee_domain = extract_domain(invitee_email)

        if inviter_domain != invitee_domain:
            return {
                "warning": True,
                "message": f"User is outside your organization domain (@{invitee_domain} vs @{inviter_domain}). Are you sure you want to invite this person?",
                "inviter_domain": inviter_domain,
                "invitee_domain": invitee_domain
            }

        return {
            "warning": False,
            "inviter_domain": inviter_domain,
            "invitee_domain": invitee_domain
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def require_role(
    allowed_roles: list,
    user_context: dict,
    supabase: Client
) -> dict:
    """
    Check if user has one of the allowed roles in their company.

    Args:
        allowed_roles: List of allowed roles (e.g., ["owner", "admin"])
        user_context: User context dict with user_id and company_id
        supabase: Supabase client (Master Supabase for multi-tenant)

    Returns:
        company_user record with role information

    Raises:
        HTTPException: 403 if user doesn't have required role
    """
    from app.core.config import Settings as MasterConfig
    master_config = MasterConfig()

    # For multi-tenant mode, check Master Supabase
    if master_config.is_multi_tenant:
        from supabase import create_client
        master_supabase = create_client(
            master_config.master_supabase_url,
            master_config.master_supabase_service_key
        )

        company_user = master_supabase.table("company_users")\
            .select("id, role, email")\
            .eq("user_id", user_context["user_id"])\
            .eq("company_id", user_context["company_id"])\
            .eq("is_active", True)\
            .maybe_single()\
            .execute()
    else:
        # Single-tenant mode fallback
        company_user = supabase.table("company_users")\
            .select("id, role, email")\
            .eq("user_id", user_context["user_id"])\
            .eq("company_id", user_context["company_id"])\
            .eq("is_active", True)\
            .maybe_single()\
            .execute()

    if not company_user.data:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this company"
        )

    user_role = company_user.data.get("role")

    if user_role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient permissions. Required roles: {', '.join(allowed_roles)}. Your role: {user_role}"
        )

    return company_user.data
