"""
Company Context Loader - Dynamic Company Information & Prompts from Master Supabase

Loads company-specific information (description, team, industries, location, etc.)
AND prompt templates from the master Supabase database.

This makes EVERYTHING dynamic:
- Company context (name, description, team, industries)
- Prompt templates (CEO assistant, email classifier, vision OCR, etc.)

Each company can customize both their data AND their prompts!
"""
import logging
from typing import Dict, List, Optional
from app.core.config_master import master_config

logger = logging.getLogger(__name__)


def _get_master_client():
    """Get master_supabase_client dynamically to avoid import-time None capture."""
    from app.core.dependencies import master_supabase_client
    return master_supabase_client

# Global cache for company context (loaded once at startup)
_company_context_cache: Optional[Dict] = None

# Global cache for prompt templates (loaded once at startup)
_prompt_templates_cache: Optional[Dict[str, str]] = None


def load_company_context() -> Dict:
    """
    Load company information from master Supabase.

    Returns dict with:
        - name: Company name
        - slug: Company slug
        - description: Company description
        - location: Company location
        - industries: List of industries served
        - capabilities: List of key capabilities
        - team: List of team members with name, title, role_description, reports_to
        - contact_name: Primary contact name
        - contact_email: Primary contact email

    If not in multi-tenant mode, returns default/empty context.
    """
    global _company_context_cache

    # Return cached context if already loaded
    if _company_context_cache is not None:
        return _company_context_cache

    # Check if multi-tenant mode is enabled
    if not master_config.is_multi_tenant:
        logger.info("📋 Single-tenant mode - no dynamic company context")
        _company_context_cache = {
            "name": "Your Company",
            "slug": "default",
            "description": "A business",
            "location": "Unknown",
            "industries": [],
            "capabilities": [],
            "team": [],
            "contact_name": "",
            "contact_email": ""
        }
        return _company_context_cache

    try:
        # Load company info from master Supabase
        logger.info(f"🔍 Loading company context for company_id: {master_config.company_id}")

        master_client = _get_master_client()
        if not master_client:
            logger.error("❌ Master Supabase client not initialized")
            _company_context_cache = _get_default_context()
            return _company_context_cache

        company_result = master_client.table("companies")\
            .select("*")\
            .eq("id", master_config.company_id)\
            .single()\
            .execute()

        if not company_result.data:
            logger.error(f"❌ Company not found in master Supabase: {master_config.company_id}")
            _company_context_cache = _get_default_context()
            return _company_context_cache

        company = company_result.data

        # Load team members from master Supabase
        team_result = master_client.table("company_team_members")\
            .select("*")\
            .eq("company_id", master_config.company_id)\
            .eq("is_active", True)\
            .execute()

        team = team_result.data or []

        # Build context
        _company_context_cache = {
            "name": company.get("name", "Your Company"),
            "slug": company.get("slug", "default"),
            "description": company.get("company_description", ""),
            "location": company.get("company_location", ""),
            "industries": company.get("industries_served", []),
            "capabilities": company.get("key_capabilities", []),
            "team": team,
            "contact_name": company.get("primary_contact_name", ""),
            "contact_email": company.get("primary_contact_email", "")
        }

        logger.info(f"✅ Loaded company context for: {_company_context_cache['name']}")
        logger.info(f"   📍 Location: {_company_context_cache['location']}")
        logger.info(f"   👥 Team members: {len(_company_context_cache['team'])}")
        logger.info(f"   🏭 Industries: {len(_company_context_cache['industries'])}")

        return _company_context_cache

    except Exception as e:
        logger.error(f"❌ Failed to load company context: {e}")
        _company_context_cache = _get_default_context()
        return _company_context_cache


def _get_default_context() -> Dict:
    """Return default context when loading fails."""
    return {
        "name": "Your Company",
        "slug": "default",
        "description": "A business",
        "location": "Unknown",
        "industries": [],
        "capabilities": [],
        "team": [],
        "contact_name": "",
        "contact_email": ""
    }


def get_company_context() -> Dict:
    """
    Get cached company context (loads if not already loaded).

    Use this function in all services that need company information.
    """
    return load_company_context()


def load_prompt_templates() -> Dict[str, str]:
    """
    Load all prompt templates from master Supabase.

    Returns dict mapping prompt_key → prompt_template text.
    Loads once and caches in memory.
    """
    global _prompt_templates_cache

    # Return cached prompts if already loaded
    if _prompt_templates_cache is not None:
        return _prompt_templates_cache

    # Check if multi-tenant mode is enabled
    if not master_config.is_multi_tenant:
        logger.info("📋 Single-tenant mode - using default prompts (not from database)")
        _prompt_templates_cache = {}
        return _prompt_templates_cache

    try:
        logger.info(f"🔍 Loading prompt templates for company_id: {master_config.company_id}")

        master_client = _get_master_client()
        if not master_client:
            logger.error("❌ Master Supabase client not initialized")
            _prompt_templates_cache = {}
            return _prompt_templates_cache

        result = master_client.table("company_prompts")\
            .select("prompt_key, prompt_template")\
            .eq("company_id", master_config.company_id)\
            .eq("is_active", True)\
            .execute()

        prompts = {row["prompt_key"]: row["prompt_template"] for row in result.data}

        _prompt_templates_cache = prompts

        logger.info(f"✅ Loaded {len(prompts)} prompt templates: {list(prompts.keys())}")

        return _prompt_templates_cache

    except Exception as e:
        logger.error(f"❌ Failed to load prompt templates: {e}")
        _prompt_templates_cache = {}
        return _prompt_templates_cache


def get_prompt_template(prompt_key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Get a specific prompt template by key.

    Args:
        prompt_key: Prompt identifier (e.g., "ceo_assistant", "email_classifier")
        default: Default template if not found

    Returns:
        Prompt template string, or None if not found
    """
    prompts = load_prompt_templates()
    return prompts.get(prompt_key, default)


def render_prompt_template(prompt_key: str, variables: Dict[str, str]) -> str:
    """
    Render a prompt template with variable substitution.

    Args:
        prompt_key: Prompt identifier
        variables: Dict of variable_name → value for {{variable_name}} placeholders

    Returns:
        Rendered prompt string with variables replaced

    Example:
        render_prompt_template("ceo_assistant", {
            "company_name": "Acme Corp",
            "context_str": "...",
            "query_str": "What materials do we use?"
        })
    """
    template = get_prompt_template(prompt_key)

    if not template:
        logger.warning(f"⚠️  Prompt template '{prompt_key}' not found")
        return ""

    # Simple variable substitution (replace {{var}} with value)
    rendered = template
    for var_name, var_value in variables.items():
        placeholder = f"{{{{{var_name}}}}}"  # {{var_name}}
        rendered = rendered.replace(placeholder, str(var_value))

    return rendered


def build_ceo_prompt_template() -> str:
    """
    Load CEO Assistant prompt template from Supabase (with fallback).

    Used by query_engine.py for response synthesis.
    """
    # Try to load template from Supabase
    logger.info("🔄 Loading ceo_assistant prompt from Supabase...")
    template = get_prompt_template("ceo_assistant")

    if template:
        logger.info("✅ Loaded ceo_assistant prompt from Supabase (version loaded dynamically)")
        return template

    # Fallback if no template in Supabase
    logger.warning("⚠️  CEO assistant prompt not found in Supabase, using fallback")
    return """You are synthesizing information from sub-questions to answer the user's original question.

Context from sub-question answers:
---------------------
{context_str}
---------------------

Original Question: {query_str}

Provide a comprehensive, conversational answer based ONLY on the information above. Do not add information not present in the context."""


def build_email_classification_context() -> str:
    """
    Build company context for email spam detection.

    NOW LOADS FROM SUPABASE! Falls back to building from context if no template found.

    Used by openai_spam_detector.py for filtering emails.
    """
    context = get_company_context()

    # Try to load template from Supabase first
    template = get_prompt_template("email_classifier")

    if template:
        logger.info("✅ Using email classifier prompt from master Supabase")

        # Build company context section
        context_lines = []

        if context["description"]:
            context_lines.append(f"- Company: {context['description']}")

        if context["capabilities"]:
            context_lines.append(f"- Specializes in: {', '.join(context['capabilities'])}")

        if context["industries"]:
            context_lines.append(f"- Industries served: {', '.join(context['industries'])}")

        company_context = "\n".join(context_lines)

        # Return the header portion (without batch_emails placeholder)
        # The actual email batch will be added by openai_spam_detector.py
        return render_prompt_template("email_classifier", {
            "company_name": context["name"],
            "company_location": context["location"],
            "company_context": company_context,
            "batch_emails": ""  # Will be filled in by openai_spam_detector
        }).rsplit("{{batch_emails}}", 1)[0]  # Remove empty batch_emails placeholder

    else:
        # Fallback: build context from scratch
        logger.warning("⚠️  Email classifier prompt not found in database, using fallback")

        lines = [
            f"You are filtering emails for {context['name']}, located in {context['location']}.",
            "",
            "COMPANY CONTEXT:"
        ]

        if context["description"]:
            lines.append(f"- Company: {context['description']}")

        if context["capabilities"]:
            lines.append(f"- Specializes in: {', '.join(context['capabilities'])}")

        if context["industries"]:
            lines.append(f"- Industries served: {', '.join(context['industries'])}")

        return "\n".join(lines)


def build_vision_ocr_context() -> str:
    """
    Build company context for GPT-4o Vision OCR (file parsing).

    NOW LOADS FROM SUPABASE! Falls back to building from context if no template found.

    Used by file_parser.py for business relevance checks.
    """
    context = get_company_context()

    # Build short description for vision prompts
    if context["description"]:
        desc = context["description"][:150]  # Keep it short for prompts
    else:
        desc = context["name"]

    if context["capabilities"]:
        desc += f" - {', '.join(context['capabilities'][:3])}"  # Top 3 capabilities

    return f"{context['name']} ({desc})"


def get_vision_ocr_business_check_prompt() -> str:
    """
    Get the full GPT-4o Vision business relevance check prompt.

    Returns the template with company context filled in.
    """
    template = get_prompt_template("vision_ocr_business_check")

    if template:
        company_short_desc = build_vision_ocr_context()
        return render_prompt_template("vision_ocr_business_check", {
            "company_short_desc": company_short_desc
        })
    else:
        # Fallback - return empty string, let file_parser use its hardcoded prompt
        return ""


def get_vision_ocr_extract_prompt() -> str:
    """
    Get the full GPT-4o Vision text extraction prompt.

    Returns the template from database (no variables needed).
    """
    return get_prompt_template("vision_ocr_extract", default="")


def get_company_name() -> str:
    """Get company name only."""
    return get_company_context()["name"]


def get_company_description() -> str:
    """Get company description only."""
    return get_company_context()["description"]


def get_company_location() -> str:
    """Get company location only."""
    return get_company_context()["location"]


def get_team_members() -> List[Dict]:
    """Get team members list only."""
    return get_company_context()["team"]
