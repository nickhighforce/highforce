"""
User Management Routes
Handles user invitations and team management for multi-tenant companies
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from datetime import datetime
from supabase import Client

from app.core.dependencies import get_supabase
from app.core.security import get_current_user_context
from app.core.validation import validate_invitation_domain, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class InviteUserRequest(BaseModel):
    """Request to invite a new user"""
    email: EmailStr
    role: str  # "admin", "user", or "viewer"


class InviteUserResponse(BaseModel):
    """Response from user invitation"""
    success: bool
    warning: bool = False
    message: Optional[str] = None
    invitee_email: str


class CompanyUser(BaseModel):
    """Company user info"""
    id: str
    user_id: str
    email: str
    role: str
    is_active: bool
    invited_by: Optional[str]
    invited_at: Optional[datetime]
    last_login_at: Optional[datetime]
    created_at: datetime


class UsersListResponse(BaseModel):
    """List of company users"""
    users: List[CompanyUser]
    total: int


@router.post("/invite", response_model=InviteUserResponse)
async def invite_user(
    invite_data: InviteUserRequest,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
):
    """
    Invite a new user to the company.

    Only owners and admins can invite users.
    Validates email domain and shows warning if outside organization domain.
    Uses Supabase's built-in invitation system.

    Args:
        invite_data: Email and role for new user
        user_context: Current user's authentication context
        supabase: Supabase client

    Returns:
        Success status and domain warning if applicable

    Raises:
        HTTPException: 403 if user lacks permission, 400 if validation fails
    """
    try:
        # Check if current user has permission to invite (owner or admin only)
        logger.info(f"🔐 Checking permissions for user {user_context['user_id'][:8]}...")
        current_user = await require_role(["owner", "admin"], user_context, supabase)
        logger.info(f"✅ Permission check passed - Role: {current_user['role']}")

        # Validate role
        valid_roles = ["admin", "user", "viewer"]
        if invite_data.role not in valid_roles:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}"
            )

        # Validate email domain and get warning if needed
        logger.info(f"📧 Validating invitation domain...")
        domain_check = validate_invitation_domain(
            current_user["email"],
            invite_data.email
        )
        logger.info(f"🔍 Domain check: {domain_check}")

        # Get Master Supabase client for invitation
        from app.core.config import Settings as MasterConfig
        from supabase import create_client

        master_config = MasterConfig()

        if not master_config.is_multi_tenant:
            raise HTTPException(
                status_code=501,
                detail="User invitations require multi-tenant mode"
            )

        # Create Master Supabase service client (needs admin API access)
        logger.info("🔑 Creating Master Supabase service client for invitation...")
        master_supabase = create_client(
            master_config.master_supabase_url,
            master_config.master_supabase_service_key
        )

        # Check if user already exists in this company
        existing_user = master_supabase.table("company_users")\
            .select("id, email, is_active")\
            .eq("email", invite_data.email)\
            .eq("company_id", user_context["company_id"])\
            .maybe_single()\
            .execute()

        if existing_user.data:
            if existing_user.data["is_active"]:
                raise HTTPException(
                    status_code=409,
                    detail="User already exists in this company"
                )
            else:
                # Reactivate inactive user
                logger.info(f"♻️ Reactivating inactive user: {invite_data.email}")
                master_supabase.table("company_users")\
                    .update({
                        "is_active": True,
                        "role": invite_data.role,
                        "invited_by": user_context["user_id"],
                        "invited_at": datetime.utcnow().isoformat()
                    })\
                    .eq("id", existing_user.data["id"])\
                    .execute()

                return InviteUserResponse(
                    success=True,
                    warning=domain_check.get("warning", False),
                    message=domain_check.get("message") if domain_check.get("warning") else "User reactivated successfully",
                    invitee_email=invite_data.email
                )

        # Send Supabase invitation email
        logger.info(f"📨 Sending Supabase invitation to {invite_data.email}...")

        try:
            # Use Supabase Admin API to invite user
            # This sends an email with a magic link
            invite_response = master_supabase.auth.admin.invite_user_by_email(
                invite_data.email,
                options={
                    "data": {
                        "company_id": user_context["company_id"],
                        "invited_by": user_context["user_id"]
                    },
                    "redirect_to": f"https://{master_config.company_id}.highforce.ai/auth/callback"
                }
            )

            logger.info(f"✅ Invitation sent successfully via Supabase")

            # Get the user_id from the invitation response
            invited_user_id = invite_response.user.id if invite_response.user else None

            # Create company_users mapping (initially inactive until they accept)
            logger.info(f"📝 Creating company_users mapping...")
            master_supabase.table("company_users").insert({
                "user_id": invited_user_id,
                "company_id": user_context["company_id"],
                "email": invite_data.email,
                "role": invite_data.role,
                "invited_by": user_context["user_id"],
                "invited_at": datetime.utcnow().isoformat(),
                "is_active": False  # Will be set to True when they accept invitation
            }).execute()

            logger.info(f"✅ User invitation complete: {invite_data.email}")

            return InviteUserResponse(
                success=True,
                warning=domain_check.get("warning", False),
                message=domain_check.get("message") if domain_check.get("warning") else "Invitation sent successfully",
                invitee_email=invite_data.email
            )

        except Exception as e:
            logger.error(f"❌ Failed to send Supabase invitation: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to send invitation: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to invite user: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=UsersListResponse)
async def list_users(
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
):
    """
    List all users in the current company.

    Returns:
        List of company users with their roles and status
    """
    try:
        from app.core.config import Settings as MasterConfig
        from supabase import create_client

        master_config = MasterConfig()

        if not master_config.is_multi_tenant:
            raise HTTPException(
                status_code=501,
                detail="User management requires multi-tenant mode"
            )

        # Get Master Supabase client
        master_supabase = create_client(
            master_config.master_supabase_url,
            master_config.master_supabase_service_key
        )

        # Get all users for this company
        logger.info(f"📋 Listing users for company: {user_context['company_id'][:8]}...")
        result = master_supabase.table("company_users")\
            .select("*")\
            .eq("company_id", user_context["company_id"])\
            .order("created_at", desc=True)\
            .execute()

        users = result.data or []
        logger.info(f"✅ Found {len(users)} users")

        return UsersListResponse(
            users=[CompanyUser(**user) for user in users],
            total=len(users)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to list users: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{user_id}")
async def remove_user(
    user_id: str,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
):
    """
    Remove a user from the company (soft delete).

    Only owners and admins can remove users.
    Sets is_active=False instead of deleting (user might belong to other companies).

    Args:
        user_id: ID of user to remove
        user_context: Current user's authentication context
        supabase: Supabase client

    Returns:
        Success message

    Raises:
        HTTPException: 403 if user lacks permission, 404 if user not found
    """
    try:
        # Check if current user has permission (owner or admin only)
        logger.info(f"🔐 Checking permissions for user {user_context['user_id'][:8]}...")
        await require_role(["owner", "admin"], user_context, supabase)

        # Prevent self-removal
        if user_id == user_context["user_id"]:
            raise HTTPException(
                status_code=400,
                detail="You cannot remove yourself from the company"
            )

        from app.core.config import Settings as MasterConfig
        from supabase import create_client

        master_config = MasterConfig()

        if not master_config.is_multi_tenant:
            raise HTTPException(
                status_code=501,
                detail="User management requires multi-tenant mode"
            )

        # Get Master Supabase client
        master_supabase = create_client(
            master_config.master_supabase_url,
            master_config.master_supabase_service_key
        )

        # Soft delete - set is_active to False
        logger.info(f"🗑️ Removing user {user_id[:8]}... from company")
        result = master_supabase.table("company_users")\
            .update({"is_active": False})\
            .eq("user_id", user_id)\
            .eq("company_id", user_context["company_id"])\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=404,
                detail="User not found in this company"
            )

        logger.info(f"✅ User removed successfully")

        return {"success": True, "message": "User removed from company"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to remove user: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
