"""
Entity Quality Filter for Knowledge Graph

Prevents low-quality "junk" entities from entering Neo4j knowledge graph.
Implements pre-insertion filtering similar to Glean's entity promotion approach.

Problem this solves:
- LlamaIndex SchemaLLMPathExtractor validates entity TYPES but not entity QUALITY
- Generic terms like "meeting", "table", "I", "we" pollute the graph
- Over time, junk entities overwhelm high-quality entities from CRM/documents

Design:
- Wraps SchemaLLMPathExtractor as LlamaIndex TransformComponent
- Filters entities BEFORE Neo4j insertion (not post-hoc cleanup)
- Simple rules-based approach (blacklist + validation patterns)
- Expected impact: 50%+ reduction in junk entities

Future improvements (Phase 2/3):
- Frequency scoring (mentioned 5+ times = likely important)
- Preloaded entity promotion (100% accurate from client docs)
- Stable entity IDs (MIDs) for long-term deduplication
"""

from llama_index.core.schema import BaseNode, TransformComponent
from llama_index.core.bridge.pydantic import Field
from typing import List, Optional, Set
import logging
import re

logger = logging.getLogger(__name__)

# Metadata key where SchemaLLMPathExtractor stores entities
KG_NODES_KEY = "__kg_nodes__"
KG_RELATIONS_KEY = "__kg_relations__"


class EntityQualityFilter(TransformComponent):
    """
    Filter low-quality entities before Neo4j insertion.

    Filtering rules:
    1. Blacklist: Generic terms (pronouns, common nouns)
    2. Length: < 3 characters rejected (e.g., "I", "we", "it")
    3. Type-specific validation:
       - PERSON: Must have 2+ words (first + last name)
       - COMPANY: Prefer entities with Corp/Inc/LLC/Ltd patterns
    4. Pattern rejection: URLs, emails, file paths

    Example:
        BEFORE: ["John Smith", "I", "meeting", "table", "Acme Corp", "we"]
        AFTER:  ["John Smith", "Acme Corp"]
    """

    # Pydantic field declarations
    blacklist: Set[str] = Field(
        default_factory=lambda: {
            # Pronouns
            "i", "we", "you", "he", "she", "it", "they", "me", "us", "them",
            # Generic nouns
            "meeting", "call", "email", "document", "file", "table", "chart",
            "team", "group", "list", "item", "thing", "stuff",
            # Time references
            "today", "yesterday", "tomorrow", "week", "month", "year",
            # Demonstratives
            "this", "that", "these", "those",
            # Articles
            "the", "a", "an",
        },
        description="Lowercase entity names to reject"
    )

    min_length: int = Field(
        default=3,
        description="Minimum character length for entity names"
    )

    enable_type_validation: bool = Field(
        default=True,
        description="Apply type-specific validation rules"
    )

    log_filtered: bool = Field(
        default=True,
        description="Log filtered entities for debugging"
    )

    def __init__(
        self,
        blacklist: Optional[Set[str]] = None,
        min_length: int = 3,
        enable_type_validation: bool = True,
        log_filtered: bool = True,
        **kwargs
    ):
        """
        Initialize EntityQualityFilter.

        Args:
            blacklist: Custom blacklist (extends default if provided)
            min_length: Minimum character length (default: 3)
            enable_type_validation: Apply type-specific rules (default: True)
            log_filtered: Log filtered entities (default: True)
        """
        # Create default blacklist
        default_blacklist = {
            "i", "we", "you", "he", "she", "it", "they", "me", "us", "them",
            "meeting", "call", "email", "document", "file", "table", "chart",
            "team", "group", "list", "item", "thing", "stuff",
            "today", "yesterday", "tomorrow", "week", "month", "year",
            "this", "that", "these", "those",
            "the", "a", "an",
        }

        # Merge custom blacklist if provided
        if blacklist:
            blacklist = default_blacklist.union(blacklist)
        else:
            blacklist = default_blacklist

        super().__init__(
            blacklist=blacklist,
            min_length=min_length,
            enable_type_validation=enable_type_validation,
            log_filtered=log_filtered,
            **kwargs
        )

        logger.info(
            f"EntityQualityFilter initialized "
            f"(blacklist={len(self.blacklist)} terms, min_length={min_length})"
        )

    def __call__(self, nodes: List[BaseNode], **kwargs) -> List[BaseNode]:
        """
        Filter entities in each node's metadata.

        Args:
            nodes: List of nodes from SchemaLLMPathExtractor

        Returns:
            Nodes with filtered entity lists
        """
        if not nodes:
            return nodes

        total_entities_before = 0
        total_entities_after = 0
        filtered_entities_log = []

        for node in nodes:
            # Get entities from metadata (set by SchemaLLMPathExtractor)
            entities = node.metadata.get(KG_NODES_KEY, [])

            if not entities:
                continue

            entities_before = len(entities)
            total_entities_before += entities_before

            # Filter entities
            filtered_entities = []
            for entity in entities:
                entity_name = entity.get("name", "")
                entity_type = entity.get("label", "UNKNOWN")

                if self._is_quality_entity(entity_name, entity_type):
                    filtered_entities.append(entity)
                else:
                    # Log rejected entity
                    if self.log_filtered:
                        filtered_entities_log.append({
                            "name": entity_name,
                            "type": entity_type,
                            "reason": self._get_rejection_reason(entity_name, entity_type)
                        })

            # Update node metadata with filtered entities
            node.metadata[KG_NODES_KEY] = filtered_entities

            entities_after = len(filtered_entities)
            total_entities_after += entities_after

            # Also filter relationships that reference rejected entities
            if KG_RELATIONS_KEY in node.metadata:
                node.metadata[KG_RELATIONS_KEY] = self._filter_relations(
                    node.metadata[KG_RELATIONS_KEY],
                    filtered_entities
                )

        # Log summary
        if total_entities_before > 0:
            rejection_rate = (total_entities_before - total_entities_after) / total_entities_before * 100
            logger.info(
                f"EntityQualityFilter: {total_entities_before} entities → {total_entities_after} entities "
                f"({rejection_rate:.1f}% rejected)"
            )

            # Log sample of rejected entities
            if self.log_filtered and filtered_entities_log:
                sample_size = min(10, len(filtered_entities_log))
                logger.debug(f"Sample rejected entities: {filtered_entities_log[:sample_size]}")

        return nodes

    def _is_quality_entity(self, entity_name: str, entity_type: str) -> bool:
        """
        Determine if entity meets quality standards.

        Args:
            entity_name: Entity name to validate
            entity_type: Entity type (PERSON, COMPANY, etc.)

        Returns:
            True if entity is high-quality, False if junk
        """
        if not entity_name or not isinstance(entity_name, str):
            return False

        # Normalize for comparison
        name_lower = entity_name.strip().lower()
        name_normalized = entity_name.strip()

        # Rule 1: Blacklist check
        if name_lower in self.blacklist:
            return False

        # Rule 2: Length check
        if len(name_normalized) < self.min_length:
            return False

        # Rule 3: Pattern rejection (URLs, emails, file paths)
        if self._matches_reject_pattern(name_normalized):
            return False

        # Rule 4: Type-specific validation
        if self.enable_type_validation:
            if not self._validate_entity_type(name_normalized, entity_type):
                return False

        return True

    def _matches_reject_pattern(self, entity_name: str) -> bool:
        """
        Check if entity matches rejection patterns.

        Reject:
        - URLs (http://, https://, www.)
        - Email addresses (contains @)
        - File paths (contains / or \\)
        - Numeric-only (e.g., "123", "2024")
        """
        # URL pattern
        if re.match(r'^(https?://|www\.)', entity_name.lower()):
            return True

        # Email pattern
        if '@' in entity_name:
            return True

        # File path pattern
        if '/' in entity_name or '\\' in entity_name:
            return True

        # Numeric-only pattern
        if entity_name.isdigit():
            return True

        return False

    def _validate_entity_type(self, entity_name: str, entity_type: str) -> bool:
        """
        Apply type-specific validation rules loaded from Supabase.

        Args:
            entity_name: Entity name
            entity_type: Entity type (PERSON, COMPANY, etc.)

        Returns:
            True if entity passes type-specific validation
        """
        # Load quality rules from config (dynamically loaded from Supabase)
        from app.services.rag.config import ENTITY_QUALITY_RULES

        # If no rules loaded or entity type not in rules, pass validation
        if not ENTITY_QUALITY_RULES or entity_type not in ENTITY_QUALITY_RULES:
            return True

        rules = ENTITY_QUALITY_RULES[entity_type]

        # Check min_words rule
        if "min_words" in rules:
            words = entity_name.split()
            if len(words) < rules["min_words"]:
                return False

        # Check reject_if_contains rule
        if "reject_if_contains" in rules:
            words = entity_name.split()
            for word in words:
                if word.lower() in rules["reject_if_contains"]:
                    return False

        # Check reject_exact rule
        if "reject_exact" in rules:
            if entity_name.lower() in rules["reject_exact"]:
                return False

        return True

    def _get_rejection_reason(self, entity_name: str, entity_type: str) -> str:
        """
        Get human-readable rejection reason for logging.

        Args:
            entity_name: Entity name
            entity_type: Entity type

        Returns:
            Rejection reason string
        """
        name_lower = entity_name.strip().lower()
        name_normalized = entity_name.strip()

        if name_lower in self.blacklist:
            return "blacklist"

        if len(name_normalized) < self.min_length:
            return f"too_short (len={len(name_normalized)})"

        if self._matches_reject_pattern(name_normalized):
            return "pattern_rejection (URL/email/path/numeric)"

        if self.enable_type_validation:
            # Load quality rules from config (dynamically loaded from Supabase)
            from app.services.rag.config import ENTITY_QUALITY_RULES

            if ENTITY_QUALITY_RULES and entity_type in ENTITY_QUALITY_RULES:
                rules = ENTITY_QUALITY_RULES[entity_type]

                # Check min_words rule
                if "min_words" in rules and len(name_normalized.split()) < rules["min_words"]:
                    return f"min_words (requires {rules['min_words']})"

                # Check reject_if_contains rule
                if "reject_if_contains" in rules:
                    for word in name_normalized.split():
                        if word.lower() in rules["reject_if_contains"]:
                            return f"contains_rejected_word ({word})"

                # Check reject_exact rule
                if "reject_exact" in rules and name_lower in rules["reject_exact"]:
                    return "generic_term"

        return "unknown"

    def _filter_relations(self, relations: List[dict], valid_entities: List[dict]) -> List[dict]:
        """
        Filter relationships that reference rejected entities.

        Args:
            relations: List of relationship dicts from SchemaLLMPathExtractor
            valid_entities: List of entities that passed quality filter

        Returns:
            Filtered relationship list
        """
        # Create set of valid entity names for fast lookup
        valid_entity_names = {e.get("name") for e in valid_entities}

        # Keep relationships where BOTH source and target are valid
        filtered_relations = []
        for relation in relations:
            source_name = relation.get("source_name", "")
            target_name = relation.get("target_name", "")

            if source_name in valid_entity_names and target_name in valid_entity_names:
                filtered_relations.append(relation)

        return filtered_relations
