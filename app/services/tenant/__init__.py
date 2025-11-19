"""
Tenant Context Management
Multi-tenant company information and prompt templates
"""
from app.services.tenant.context import (
    load_company_context,
    get_company_context,
    load_prompt_templates,
    get_prompt_template,
    render_prompt_template,
    build_ceo_prompt_template,
    build_email_classification_context,
    build_vision_ocr_context,
    get_vision_ocr_business_check_prompt,
    get_vision_ocr_extract_prompt,
    get_company_name,
    get_company_description,
    get_company_location,
    get_team_members,
)

__all__ = [
    "load_company_context",
    "get_company_context",
    "load_prompt_templates",
    "get_prompt_template",
    "render_prompt_template",
    "build_ceo_prompt_template",
    "build_email_classification_context",
    "build_vision_ocr_context",
    "get_vision_ocr_business_check_prompt",
    "get_vision_ocr_extract_prompt",
    "get_company_name",
    "get_company_description",
    "get_company_location",
    "get_team_members",
]
