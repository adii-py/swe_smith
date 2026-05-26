"""
Grounded Rust Bug Generation System for SWE-Smith.

A repository-aware bug generation pipeline that:
1. Parses Rust code using tree-sitter
2. Builds dependency/call graphs
3. Retrieves related context
4. Generates grounded, compilable bugs
5. Validates patches
"""

from .pipeline import GroundedBugPipeline
from .parser.repository_parser import RepositoryParser
from .graph.builder import GraphBuilder
from .retrieval.context_retriever import ContextRetriever
from .generator.bug_generator import BugGenerator
from .validator.patch_validator import PatchValidator

__all__ = [
    "GroundedBugPipeline",
    "RepositoryParser",
    "GraphBuilder",
    "ContextRetriever",
    "BugGenerator",
    "PatchValidator",
]
