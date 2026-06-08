"""
Base Memory Provider Abstract Classes
Defines interfaces for short-term and long-term memory providers
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum


class MemoryType(Enum):
    """Types of memory entries"""
    TASK = "task"
    PLAN = "plan"
    CONVERSATION = "conversation"
    CONTEXT = "context"
    EMBEDDING = "embedding"


@dataclass
class MemoryEntry:
    """
    Represents a memory entry that can be stored and retrieved.
    Used for both short-term and long-term memory.
    """
    id: str
    content: str
    memory_type: MemoryType
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    ttl: Optional[int] = None  # Time-to-live in seconds (for short-term memory)
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "embedding": self.embedding,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ttl": self.ttl,
            "session_id": self.session_id,
            "user_id": self.user_id,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        """Create from dictionary"""
        return cls(
            id=data["id"],
            content=data["content"],
            memory_type=MemoryType(data.get("memory_type", "context")),
            embedding=data.get("embedding"),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", datetime.utcnow().isoformat()),
            updated_at=data.get("updated_at", datetime.utcnow().isoformat()),
            ttl=data.get("ttl"),
            session_id=data.get("session_id"),
            user_id=data.get("user_id"),
        )


@dataclass
class MemorySearchResult:
    """Result from a memory search operation"""
    entry: MemoryEntry
    score: float  # Similarity score (0-1 for cosine similarity)
    source: str  # Which memory provider returned this result


class MemoryProvider(ABC):
    """
    Abstract base class for memory providers.
    Implementations should provide storage and retrieval of memory entries.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the memory provider"""
        pass
    
    @property
    @abstractmethod
    def is_short_term(self) -> bool:
        """Whether this is a short-term memory provider"""
        pass
    
    @abstractmethod
    async def store(self, entry: MemoryEntry) -> str:
        """
        Store a memory entry.
        
        Args:
            entry: The memory entry to store
            
        Returns:
            The ID of the stored entry
        """
        pass
    
    @abstractmethod
    async def retrieve(self, entry_id: str) -> Optional[MemoryEntry]:
        """
        Retrieve a specific memory entry by ID.
        
        Args:
            entry_id: The ID of the entry to retrieve
            
        Returns:
            The memory entry if found, None otherwise
        """
        pass
    
    @abstractmethod
    async def search(
        self,
        query_embedding: List[float],
        limit: int = 10,
        threshold: float = 0.7,
        memory_type: Optional[MemoryType] = None,
        session_id: Optional[str] = None,
    ) -> List[MemorySearchResult]:
        """
        Search for similar memory entries using vector similarity.
        
        Args:
            query_embedding: The embedding vector to search with
            limit: Maximum number of results to return
            threshold: Minimum similarity score (0-1)
            memory_type: Filter by memory type
            session_id: Filter by session ID
            
        Returns:
            List of search results sorted by similarity
        """
        pass
    
    @abstractmethod
    async def search_by_text(
        self,
        query: str,
        limit: int = 10,
        memory_type: Optional[MemoryType] = None,
        session_id: Optional[str] = None,
    ) -> List[MemorySearchResult]:
        """
        Search for memory entries by text query.
        This method should handle embedding generation internally.
        
        Args:
            query: The text query to search for
            limit: Maximum number of results to return
            memory_type: Filter by memory type
            session_id: Filter by session ID
            
        Returns:
            List of search results sorted by relevance
        """
        pass
    
    @abstractmethod
    async def delete(self, entry_id: str) -> bool:
        """
        Delete a memory entry.
        
        Args:
            entry_id: The ID of the entry to delete
            
        Returns:
            True if deleted, False if not found
        """
        pass
    
    @abstractmethod
    async def list_by_session(
        self,
        session_id: str,
        limit: int = 100,
        memory_type: Optional[MemoryType] = None,
    ) -> List[MemoryEntry]:
        """
        List memory entries for a specific session.
        
        Args:
            session_id: The session ID to filter by
            limit: Maximum number of results to return
            memory_type: Filter by memory type
            
        Returns:
            List of memory entries
        """
        pass
    
    @abstractmethod
    async def clear_session(self, session_id: str) -> int:
        """
        Clear all memory entries for a session.
        
        Args:
            session_id: The session ID to clear
            
        Returns:
            Number of entries deleted
        """
        pass
    
    async def health_check(self) -> bool:
        """
        Check if the memory provider is healthy.
        
        Returns:
            True if healthy, False otherwise
        """
        return True


class CompositeMemory:
    """
    Combines multiple memory providers (short-term and long-term).
    Coordinates storage and retrieval across providers.
    """
    
    def __init__(
        self,
        short_term: Optional[MemoryProvider] = None,
        long_term: Optional[MemoryProvider] = None,
    ):
        self.short_term = short_term
        self.long_term = long_term
    
    async def store(
        self,
        entry: MemoryEntry,
        persist_to_long_term: bool = False,
    ) -> Dict[str, str]:
        """
        Store a memory entry in appropriate providers.
        
        Args:
            entry: The memory entry to store
            persist_to_long_term: Whether to also store in long-term memory
            
        Returns:
            Dictionary of provider names to entry IDs
        """
        results = {}
        
        if self.short_term:
            entry_id = await self.short_term.store(entry)
            results[self.short_term.name] = entry_id
        
        if persist_to_long_term and self.long_term:
            entry_id = await self.long_term.store(entry)
            results[self.long_term.name] = entry_id
        
        return results
    
    async def search(
        self,
        query_embedding: List[float],
        limit: int = 10,
        threshold: float = 0.7,
        include_short_term: bool = True,
        include_long_term: bool = True,
        memory_type: Optional[MemoryType] = None,
        session_id: Optional[str] = None,
    ) -> List[MemorySearchResult]:
        """
        Search across memory providers.
        
        Args:
            query_embedding: The embedding vector to search with
            limit: Maximum number of results per provider
            threshold: Minimum similarity score
            include_short_term: Whether to search short-term memory
            include_long_term: Whether to search long-term memory
            memory_type: Filter by memory type
            session_id: Filter by session ID
            
        Returns:
            Combined list of search results sorted by score
        """
        results = []
        
        if include_short_term and self.short_term:
            short_results = await self.short_term.search(
                query_embedding=query_embedding,
                limit=limit,
                threshold=threshold,
                memory_type=memory_type,
                session_id=session_id,
            )
            results.extend(short_results)
        
        if include_long_term and self.long_term:
            long_results = await self.long_term.search(
                query_embedding=query_embedding,
                limit=limit,
                threshold=threshold,
                memory_type=memory_type,
                session_id=session_id,
            )
            results.extend(long_results)
        
        # Sort by score and deduplicate
        results.sort(key=lambda x: x.score, reverse=True)
        
        # Deduplicate by content (keep highest scoring)
        seen_content = set()
        unique_results = []
        for result in results:
            if result.entry.content not in seen_content:
                seen_content.add(result.entry.content)
                unique_results.append(result)
        
        return unique_results[:limit]
    
    async def promote_to_long_term(self, entry_id: str) -> Optional[str]:
        """
        Promote a short-term memory to long-term storage.
        
        Args:
            entry_id: The ID of the short-term entry to promote
            
        Returns:
            The ID of the long-term entry, or None if failed
        """
        if not self.short_term or not self.long_term:
            return None
        
        entry = await self.short_term.retrieve(entry_id)
        if not entry:
            return None
        
        # Remove TTL for long-term storage
        entry.ttl = None
        entry.updated_at = datetime.utcnow().isoformat()
        
        return await self.long_term.store(entry)
    
    async def health_check(self) -> Dict[str, bool]:
        """Check health of all providers"""
        results = {}
        
        if self.short_term:
            results[self.short_term.name] = await self.short_term.health_check()
        
        if self.long_term:
            results[self.long_term.name] = await self.long_term.health_check()
        
        return results
