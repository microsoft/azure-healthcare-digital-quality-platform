"""
Memory Provider Module for MCP Server
Provides short-term (CosmosDB), long-term (AI Search, FoundryIQ), and facts (Fabric IQ) memory abstractions

Architecture:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              CompositeMemory                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────────┐ │
│  │  Short-Term Memory  │  │   Long-Term Memory  │  │     Facts Memory        │ │
│  │    (CosmosDB)       │  │   (AI Search)       │  │    (Fabric IQ)          │ │
│  │  - Session-based    │  │  - Persistent       │  │  - Ontology-grounded    │ │
│  │  - TTL support      │  │  - Cross-session    │  │  - Cross-domain         │ │
│  │  - Fast access      │  │  - Hybrid search    │  │  - Entity relationships │ │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘

Domain Ontologies:
- Healthcare Quality: Members, measures, care gaps, providers, interventions
"""

from .base import (
    MemoryProvider,
    MemoryEntry,
    MemorySearchResult,
    MemoryType,
    CompositeMemory,
)
from .cosmos_memory import ShortTermMemory

try:
    from .aisearch_memory import LongTermMemory, AISEARCH_CONTEXT_PROVIDER_AVAILABLE
except ImportError:
    LongTermMemory = None  # type: ignore[assignment]
    AISEARCH_CONTEXT_PROVIDER_AVAILABLE = False

try:
    from .facts_memory import (
        FactsMemory,
        Fact,
        FactSearchResult,
        OntologyEntity,
        OntologyRelationship,
        EntityType,
        RelationshipType,
    )
except ImportError:
    FactsMemory = None  # type: ignore[assignment]
    Fact = None  # type: ignore[assignment]
    FactSearchResult = None  # type: ignore[assignment]
    OntologyEntity = None  # type: ignore[assignment]
    OntologyRelationship = None  # type: ignore[assignment]
    EntityType = None  # type: ignore[assignment]
    RelationshipType = None  # type: ignore[assignment]

__all__ = [
    # Base classes
    "MemoryProvider",
    "MemoryEntry",
    "MemorySearchResult",
    "MemoryType",
    "CompositeMemory",
    # Short-term memory (CosmosDB)
    "ShortTermMemory",
    # Long-term memory (AI Search with AzureAISearchContextProvider)
    "LongTermMemory",
    "AISEARCH_CONTEXT_PROVIDER_AVAILABLE",
    # Facts memory (Fabric IQ)
    "FactsMemory",
    "Fact",
    "FactSearchResult",
    "OntologyEntity",
    "OntologyRelationship",
    "EntityType",
    "RelationshipType",
]
