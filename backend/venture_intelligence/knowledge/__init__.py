"""Persistent JSON-canonical knowledge/wiki compilation."""

from .compiler import KnowledgeCompiler, KnowledgeValidationReport
from .evidence import EvidenceCategory, normalize_evidence, summarize_evidence
from .models import ClaimStatus, KnowledgeClaim, KnowledgePage

__all__ = [
    "ClaimStatus",
    "EvidenceCategory",
    "KnowledgeClaim",
    "KnowledgeCompiler",
    "KnowledgePage",
    "KnowledgeValidationReport",
    "normalize_evidence",
    "summarize_evidence",
]
