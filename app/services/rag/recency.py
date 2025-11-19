"""
Recency Boost Postprocessor for LlamaIndex

Applies exponential decay to favor recent documents over old ones.
Prevents stale data from polluting query results over time.

Research: Based on LlamaIndex community best practices (GitHub Discussion #8446)
Formula: score * (0.5 ** (age_days / decay_days))
"""

from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle
from datetime import datetime
from typing import List, Optional
from pydantic import Field
import logging

logger = logging.getLogger(__name__)


class RecencyBoostPostprocessor(BaseNodePostprocessor):
    """
    Boost recent documents with exponential decay.

    Uses exponential decay to favor recent documents:
    - Documents from today: 100% score
    - Documents from decay_days ago: 50% score
    - Documents from 2*decay_days ago: 25% score

    Example:
        With decay_days=90:
        - 30 days old: score * 0.79 (79% of original)
        - 90 days old: score * 0.50 (50% of original)
        - 180 days old: score * 0.25 (25% of original)
        - 365 days old: score * 0.06 (6% of original)

    This prevents old data (e.g., "John manages Acme") from ranking higher
    than recent data (e.g., "Mary now manages Acme") when both have similar
    semantic similarity scores.
    """

    # Pydantic field declarations
    decay_days: int = Field(
        default=90,
        description="Number of days for score to decay to 50%"
    )
    timestamp_key: str = Field(
        default="created_at_timestamp",
        description="Metadata key containing Unix timestamp"
    )

    def __init__(
        self,
        decay_days: int = 90,
        timestamp_key: str = "created_at_timestamp",
        **kwargs
    ):
        """
        Initialize RecencyBoostPostprocessor.

        Args:
            decay_days: Number of days for score to decay to 50%.
                       Smaller = more aggressive decay (favor very recent).
                       Larger = gentler decay (consider older docs).
                       Recommended: 90 for business data, 30 for news/social.
            timestamp_key: Metadata key containing Unix timestamp.
                          Default: "created_at_timestamp"
        """
        super().__init__(decay_days=decay_days, timestamp_key=timestamp_key, **kwargs)
        logger.info(f"RecencyBoostPostprocessor initialized (decay_days={decay_days})")

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None
    ) -> List[NodeWithScore]:
        """
        Apply recency boost to node scores.

        Args:
            nodes: List of nodes with similarity scores
            query_bundle: Optional query information (unused)

        Returns:
            Nodes with boosted scores, re-sorted by new scores
        """
        if not nodes:
            return nodes

        now_ts = datetime.now().timestamp()
        boosted_count = 0
        skipped_count = 0

        for node in nodes:
            # Get document timestamp from metadata
            created_at_ts = node.node.metadata.get(self.timestamp_key)

            if created_at_ts:
                # Calculate age in days
                age_seconds = now_ts - created_at_ts
                age_days = age_seconds / (60 * 60 * 24)

                # Exponential decay: 100% at 0 days, 50% at decay_days
                # Formula: 0.5 ** (age_days / decay_days)
                recency_score = 0.5 ** (age_days / self.decay_days)

                # Boost original score with recency
                original_score = node.score
                node.score = node.score * recency_score

                boosted_count += 1

                # Log significant boosts/penalties
                if recency_score < 0.3 or age_days < 7:
                    logger.debug(
                        f"Recency boost applied: age={age_days:.1f} days, "
                        f"original_score={original_score:.4f}, "
                        f"recency_score={recency_score:.4f}, "
                        f"final_score={node.score:.4f}"
                    )
            else:
                # No timestamp = no boost (keep original score)
                skipped_count += 1

        # Re-sort by new scores (highest first)
        nodes.sort(key=lambda x: x.score, reverse=True)

        logger.info(
            f"RecencyBoost: Boosted {boosted_count} nodes, "
            f"skipped {skipped_count} (no timestamp)"
        )

        return nodes


class DocumentTypeRecencyPostprocessor(BaseNodePostprocessor):
    """
    Document-type-aware recency boost with different decay rates per document type.

    Research: Production RAG systems (5M+ documents) use type-specific decay:
    - Emails: Aggressive decay (30 days) - conversations get stale fast
    - Attachments: Moderate decay (90 days) - could be important reference docs
    - Default: Fallback to 90 days for unknown types

    This prevents old emails from polluting results while preserving evergreen content
    like contracts, specifications, and reference materials.

    Example with 60-day-old content:
    - Email (30-day decay): score * 0.25 (75% penalty - very stale)
    - Attachment (90-day decay): score * 0.63 (37% penalty - still relevant)

    Verified against actual codebase (AUDIT_RESULTS.md):
    - document_type metadata: ✅ PRESENT in Qdrant payloads
    - Only 2 types in production: "email" and "attachment"
    - Backward compatible: Falls back to 90-day decay if document_type missing
    """

    # Pydantic field declarations
    decay_profiles: dict = Field(
        default_factory=lambda: {
            "email": 30,        # Aggressive decay - emails get stale fast
            "attachment": 90,   # Moderate decay - could be important files
        },
        description="Decay days per document type"
    )
    default_decay_days: int = Field(
        default=90,
        description="Default decay for unknown document types"
    )
    timestamp_key: str = Field(
        default="created_at_timestamp",
        description="Metadata key containing Unix timestamp"
    )
    document_type_key: str = Field(
        default="document_type",
        description="Metadata key containing document type"
    )

    def __init__(
        self,
        decay_profiles: Optional[dict] = None,
        default_decay_days: int = 90,
        timestamp_key: str = "created_at_timestamp",
        document_type_key: str = "document_type",
        **kwargs
    ):
        """
        Initialize DocumentTypeRecencyPostprocessor.

        Args:
            decay_profiles: Dict mapping document types to decay days.
                          Example: {"email": 30, "attachment": 90}
            default_decay_days: Fallback decay for unknown types
            timestamp_key: Metadata key for Unix timestamp
            document_type_key: Metadata key for document type
        """
        if decay_profiles is None:
            decay_profiles = {
                "email": 30,
                "attachment": 90,
            }

        super().__init__(
            decay_profiles=decay_profiles,
            default_decay_days=default_decay_days,
            timestamp_key=timestamp_key,
            document_type_key=document_type_key,
            **kwargs
        )
        logger.info(f"DocumentTypeRecencyPostprocessor initialized: {decay_profiles}")

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None
    ) -> List[NodeWithScore]:
        """
        Apply document-type-aware recency boost to node scores.

        Args:
            nodes: List of nodes with similarity scores
            query_bundle: Optional query information (unused)

        Returns:
            Nodes with boosted scores, re-sorted by new scores
        """
        if not nodes:
            return nodes

        now_ts = datetime.now().timestamp()
        boosted_count = 0
        skipped_count = 0
        type_stats = {}  # Track boosts per document type

        # BEFORE: Log top 5 nodes before recency adjustment
        logger.info("📊 BEFORE Recency Decay (Top 5):")
        for i, node in enumerate(nodes[:5], 1):
            meta = node.node.metadata if hasattr(node, 'node') else {}
            created_ts = meta.get(self.timestamp_key, now_ts)
            age_days = (now_ts - created_ts) / 86400
            dtype = meta.get(self.document_type_key, 'unknown')
            text = (node.node.text if hasattr(node, 'node') and hasattr(node.node, 'text') else str(node))[:40]
            logger.info(f"  {i}. score={node.score:.8f} age={age_days:.1f}d type={dtype} | {text}...")

        for node in nodes:
            # Get document type and timestamp from metadata
            doc_type = node.node.metadata.get(self.document_type_key, "").lower()
            created_at_ts = node.node.metadata.get(self.timestamp_key)

            if not created_at_ts:
                # No timestamp = no boost (keep original score)
                skipped_count += 1
                continue

            # Get decay days for this document type
            decay_days = self.decay_profiles.get(doc_type, self.default_decay_days)

            # Calculate age in days
            age_seconds = now_ts - created_at_ts
            age_days = age_seconds / (60 * 60 * 24)

            # Exponential decay: 100% at 0 days, 50% at decay_days
            recency_score = 0.5 ** (age_days / decay_days)

            # Boost original score with recency
            original_score = node.score
            node.score = node.score * recency_score

            boosted_count += 1

            # Track stats per type
            if doc_type not in type_stats:
                type_stats[doc_type] = {"count": 0, "avg_age": 0, "avg_boost": 0}
            type_stats[doc_type]["count"] += 1
            type_stats[doc_type]["avg_age"] += age_days
            type_stats[doc_type]["avg_boost"] += recency_score

            # Log significant boosts/penalties
            if recency_score < 0.3 or age_days < 7:
                logger.debug(
                    f"Type-aware boost: type={doc_type or 'unknown'}, age={age_days:.1f}d, "
                    f"decay={decay_days}d, original={original_score:.4f}, "
                    f"boost={recency_score:.4f}, final={node.score:.4f}"
                )

        # Re-sort by new scores (highest first)
        nodes.sort(key=lambda x: x.score, reverse=True)

        # AFTER: Log top 5 nodes after recency adjustment
        logger.info("📊 AFTER Recency Decay (Top 5 - re-sorted):")
        for i, node in enumerate(nodes[:5], 1):
            meta = node.node.metadata if hasattr(node, 'node') else {}
            created_ts = meta.get(self.timestamp_key, now_ts)
            age_days = (now_ts - created_ts) / 86400
            dtype = meta.get(self.document_type_key, 'unknown')
            text = (node.node.text if hasattr(node, 'node') and hasattr(node.node, 'text') else str(node))[:40]
            logger.info(f"  {i}. score={node.score:.8f} age={age_days:.1f}d type={dtype} | {text}...")

        # Log summary stats
        for doc_type, stats in type_stats.items():
            count = stats["count"]
            avg_age = stats["avg_age"] / count
            avg_boost = stats["avg_boost"] / count
            logger.info(
                f"DocumentTypeRecency [{doc_type or 'unknown'}]: "
                f"{count} nodes, avg_age={avg_age:.1f}d, avg_boost={avg_boost:.3f}"
            )

        logger.info(
            f"DocumentTypeRecency: Boosted {boosted_count} nodes, "
            f"skipped {skipped_count} (no timestamp)"
        )

        return nodes
