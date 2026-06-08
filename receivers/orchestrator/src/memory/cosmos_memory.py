"""
CosmosDB Short-Term Memory Provider
Provides ephemeral, session-based memory storage with vector similarity search
"""

import logging
import numpy as np
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable

from azure.cosmos import CosmosClient, ContainerProxy, exceptions as cosmos_exceptions
from azure.identity import DefaultAzureCredential

from .base import MemoryProvider, MemoryEntry, MemorySearchResult, MemoryType

logger = logging.getLogger(__name__)


class ShortTermMemory(MemoryProvider):
    """
    Short-term memory provider backed by Azure CosmosDB.
    
    Features:
    - Session-based memory isolation
    - TTL support for automatic expiration
    - Vector similarity search using cosine similarity
    - Async operations for non-blocking I/O
    
    CosmosDB Container Requirements:
    - Partition key: /session_id
    - Vector embedding policy on /embedding field
    - TTL enabled for automatic cleanup
    """
    
    def __init__(
        self,
        endpoint: str,
        database_name: str,
        container_name: str = "chat",
        credential: Optional[Any] = None,
        embedding_function: Optional[Callable[[str], List[float]]] = None,
        default_ttl: int = 3600,  # 1 hour default
    ):
        """
        Initialize CosmosDB short-term memory provider.
        
        Args:
            endpoint: CosmosDB endpoint URL
            database_name: Name of the database
            container_name: Name of the container for short-term memory
            credential: Azure credential (uses DefaultAzureCredential if not provided)
            embedding_function: Function to generate embeddings from text
            default_ttl: Default time-to-live in seconds (1 hour)
        """
        self._endpoint = endpoint
        self._database_name = database_name
        self._container_name = container_name
        self._default_ttl = default_ttl
        self._embedding_function = embedding_function
        
        # Initialize CosmosDB client
        if credential is None:
            credential = DefaultAzureCredential()
        
        self._client = CosmosClient(endpoint, credential=credential)
        self._database = self._client.get_database_client(database_name)
        self._container: ContainerProxy = self._database.get_container_client(container_name)
        
        logger.info(f"CosmosDB Short-Term Memory initialized: {database_name}/{container_name}")
    
    @property
    def name(self) -> str:
        return "cosmos_short_term"
    
    @property
    def is_short_term(self) -> bool:
        return True
    
    def set_embedding_function(self, func: Callable[[str], List[float]]) -> None:
        """Set the embedding function for text-to-vector conversion"""
        self._embedding_function = func
    
    async def store(self, entry: MemoryEntry) -> str:
        """Store a memory entry in CosmosDB"""
        try:
            # Set TTL if not specified
            if entry.ttl is None:
                entry.ttl = self._default_ttl
            
            # Ensure session_id is set (required for partition key)
            if not entry.session_id:
                entry.session_id = "default"
            
            entry.updated_at = datetime.utcnow().isoformat()
            
            # Convert to CosmosDB document format
            doc = entry.to_dict()
            doc["ttl"] = entry.ttl  # CosmosDB TTL field
            
            # Upsert the document
            self._container.upsert_item(doc)
            
            logger.debug(f"Stored memory entry: {entry.id} in session: {entry.session_id}")
            return entry.id
            
        except cosmos_exceptions.CosmosHttpResponseError as e:
            logger.error(f"Failed to store memory entry: {e.message}")
            raise
    
    async def retrieve(self, entry_id: str) -> Optional[MemoryEntry]:
        """Retrieve a memory entry by ID"""
        try:
            # Query across all partitions since we don't know the session_id
            query = "SELECT * FROM c WHERE c.id = @id"
            items = list(self._container.query_items(
                query=query,
                parameters=[{"name": "@id", "value": entry_id}],
                enable_cross_partition_query=True
            ))
            
            if items:
                return MemoryEntry.from_dict(items[0])
            return None
            
        except cosmos_exceptions.CosmosHttpResponseError as e:
            logger.error(f"Failed to retrieve memory entry {entry_id}: {e.message}")
            return None
    
    async def search(
        self,
        query_embedding: List[float],
        limit: int = 10,
        threshold: float = 0.7,
        memory_type: Optional[MemoryType] = None,
        session_id: Optional[str] = None,
    ) -> List[MemorySearchResult]:
        """Search for similar memory entries using cosine similarity"""
        try:
            # Build query with optional filters
            query_parts = ["SELECT * FROM c WHERE IS_DEFINED(c.embedding)"]
            parameters = []
            
            if memory_type:
                query_parts.append("AND c.memory_type = @memory_type")
                parameters.append({"name": "@memory_type", "value": memory_type.value})
            
            if session_id:
                query_parts.append("AND c.session_id = @session_id")
                parameters.append({"name": "@session_id", "value": session_id})
            
            query = " ".join(query_parts)
            
            # Execute query
            items = list(self._container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))
            
            # Calculate cosine similarity for each item
            results = []
            query_vec = np.array(query_embedding)
            query_norm = np.linalg.norm(query_vec)
            
            if query_norm == 0:
                return []
            
            for item in items:
                if not item.get("embedding"):
                    continue
                
                item_vec = np.array(item["embedding"])
                item_norm = np.linalg.norm(item_vec)
                
                if item_norm == 0:
                    continue
                
                similarity = float(np.dot(query_vec, item_vec) / (query_norm * item_norm))
                
                if similarity >= threshold:
                    results.append(MemorySearchResult(
                        entry=MemoryEntry.from_dict(item),
                        score=similarity,
                        source=self.name
                    ))
            
            # Sort by similarity descending
            results.sort(key=lambda x: x.score, reverse=True)
            return results[:limit]
            
        except cosmos_exceptions.CosmosHttpResponseError as e:
            logger.error(f"Failed to search memory: {e.message}")
            return []
    
    async def search_by_text(
        self,
        query: str,
        limit: int = 10,
        memory_type: Optional[MemoryType] = None,
        session_id: Optional[str] = None,
    ) -> List[MemorySearchResult]:
        """Search for memory entries by text query"""
        if not self._embedding_function:
            raise ValueError("Embedding function not configured")
        
        # Generate embedding for the query
        query_embedding = self._embedding_function(query)
        
        return await self.search(
            query_embedding=query_embedding,
            limit=limit,
            threshold=0.7,
            memory_type=memory_type,
            session_id=session_id,
        )
    
    async def delete(self, entry_id: str) -> bool:
        """Delete a memory entry"""
        try:
            # First retrieve to get partition key
            entry = await self.retrieve(entry_id)
            if not entry:
                return False
            
            self._container.delete_item(
                item=entry_id,
                partition_key=entry.session_id or "default"
            )
            
            logger.debug(f"Deleted memory entry: {entry_id}")
            return True
            
        except cosmos_exceptions.CosmosHttpResponseError as e:
            logger.error(f"Failed to delete memory entry {entry_id}: {e.message}")
            return False
    
    async def list_by_session(
        self,
        session_id: str,
        limit: int = 100,
        memory_type: Optional[MemoryType] = None,
    ) -> List[MemoryEntry]:
        """List memory entries for a specific session"""
        try:
            query_parts = ["SELECT * FROM c WHERE c.session_id = @session_id"]
            parameters = [{"name": "@session_id", "value": session_id}]
            
            if memory_type:
                query_parts.append("AND c.memory_type = @memory_type")
                parameters.append({"name": "@memory_type", "value": memory_type.value})
            
            query_parts.append("ORDER BY c.created_at DESC")
            query_parts.append(f"OFFSET 0 LIMIT {limit}")
            
            query = " ".join(query_parts)
            
            items = list(self._container.query_items(
                query=query,
                parameters=parameters,
                partition_key=session_id
            ))
            
            return [MemoryEntry.from_dict(item) for item in items]
            
        except cosmos_exceptions.CosmosHttpResponseError as e:
            logger.error(f"Failed to list session memory: {e.message}")
            return []
    
    async def clear_session(self, session_id: str) -> int:
        """Clear all memory entries for a session"""
        try:
            # Get all items in the session
            query = "SELECT c.id FROM c WHERE c.session_id = @session_id"
            items = list(self._container.query_items(
                query=query,
                parameters=[{"name": "@session_id", "value": session_id}],
                partition_key=session_id
            ))
            
            count = 0
            for item in items:
                try:
                    self._container.delete_item(
                        item=item["id"],
                        partition_key=session_id
                    )
                    count += 1
                except cosmos_exceptions.CosmosHttpResponseError:
                    pass
            
            logger.info(f"Cleared {count} memory entries for session: {session_id}")
            return count
            
        except cosmos_exceptions.CosmosHttpResponseError as e:
            logger.error(f"Failed to clear session memory: {e.message}")
            return 0
    
    async def health_check(self) -> bool:
        """Check if CosmosDB connection is healthy"""
        try:
            # Try to read container properties
            self._container.read()
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    async def store_conversation_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Convenience method to store a conversation turn.
        
        Args:
            session_id: The session ID
            role: Role (user, assistant, system)
            content: The message content
            embedding: Optional pre-computed embedding
            metadata: Optional additional metadata
            
        Returns:
            The ID of the stored entry
        """
        import uuid
        
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            content=content,
            memory_type=MemoryType.CONVERSATION,
            embedding=embedding,
            session_id=session_id,
            metadata={
                "role": role,
                **(metadata or {})
            }
        )
        
        return await self.store(entry)
    
    async def get_conversation_history(
        self,
        session_id: str,
        limit: int = 20,
    ) -> List[Dict[str, str]]:
        """
        Get conversation history for a session.
        
        Args:
            session_id: The session ID
            limit: Maximum number of messages to return
            
        Returns:
            List of messages with role and content
        """
        entries = await self.list_by_session(
            session_id=session_id,
            limit=limit,
            memory_type=MemoryType.CONVERSATION
        )
        
        # Reverse to get chronological order
        entries.reverse()
        
        return [
            {
                "role": entry.metadata.get("role", "user"),
                "content": entry.content
            }
            for entry in entries
        ]
    
    async def find_relevant_context(
        self,
        query: str,
        session_id: str,
        limit: int = 5,
    ) -> List[str]:
        """
        Find relevant context from session memory for a query.
        
        Args:
            query: The query to find context for
            session_id: The session ID
            limit: Maximum number of context items
            
        Returns:
            List of relevant content strings
        """
        if not self._embedding_function:
            return []
        
        results = await self.search_by_text(
            query=query,
            limit=limit,
            session_id=session_id
        )
        
        return [r.entry.content for r in results]
