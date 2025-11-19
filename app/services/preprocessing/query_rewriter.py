"""
Query Rewriting - Converts vague follow-up queries into explicit searchable queries
"""
import os
import logging
from typing import List, Dict, Optional
from openai import OpenAI

logger = logging.getLogger(__name__)


# Lazy initialize OpenAI client (loaded from config after startup)
_openai_client: Optional[OpenAI] = None

def get_openai_client() -> OpenAI:
    """Get or create OpenAI client (lazy initialization)"""
    global _openai_client
    if _openai_client is None:
        from app.core.config import settings
        _openai_client = OpenAI(api_key=settings.openai_api_key)
    return _openai_client


def rewrite_query_with_context(
    query: str,
    conversation_history: List[Dict] = []
) -> str:
    """
    Rewrite vague follow-up queries into explicit, searchable queries
    using conversation history for context resolution.

    This is critical for RAG systems to handle follow-up questions like:
    - "tell me more about this doc" → "Q4 2025 Sales Strategy document Sarah Chen"
    - "what about that deal?" → "MedTech Solutions enterprise deal details"
    - "who is working on it?" → "TechCorp Strategic Partnership team members"

    Args:
        query: The user's original query (may be vague)
        conversation_history: List of previous messages with 'role' and 'content' keys

    Returns:
        str: Rewritten query with explicit entities and context
    """
    if not conversation_history:
        return query  # No context, use original query

    # Build conversation context from recent messages
    context_messages = []
    for msg in conversation_history[-6:]:  # Last 3 exchanges
        role_label = "User" if msg["role"] == "user" else "Assistant"
        context_messages.append(f"{role_label}: {msg['content'][:200]}")

    conversation_context = "\n".join(context_messages)

    system_prompt = """You are a query rewriting assistant. Your job is to rewrite vague follow-up questions into explicit, searchable queries.

RULES:
1. Extract entity names, document names, and specific topics from conversation history
2. Replace pronouns (this, that, he, she, it) with actual entity names
3. Replace vague references (this document, that person, the deal) with specific names
4. Keep the query concise but information-rich
5. If the query is already explicit, return it unchanged
6. Focus on WHAT the user wants to search for, not what they want to know

Examples:
- "tell me more about this doc" → "Q4 2025 Sales Strategy document Sarah Chen"
- "what about that deal?" → "MedTech Solutions enterprise deal details"
- "who is working on it?" → "TechCorp Strategic Partnership team members"
- "what's the pricing?" → "DataFlow Inc pricing strategy competitive analysis"
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"""Conversation history:
{conversation_context}

Current query: {query}

Rewrite this query to be explicit and searchable. Return ONLY the rewritten query, nothing else."""}
    ]

    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.3,  # Lower temperature for consistent rewrites
            max_tokens=100
        )
        rewritten = response.choices[0].message.content.strip()

        # Remove quotes if LLM adds them
        rewritten = rewritten.strip('"').strip("'")

        return rewritten

    except Exception as e:
        logger.warning(f"⚠️  Query rewriting failed: {str(e)}")
        return query  # Fallback to original query on error
