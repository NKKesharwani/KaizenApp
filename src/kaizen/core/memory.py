"""Memory Manager for Kaizen: Persistent conversation history and long-term memory."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional
from sqlalchemy.orm import Session

from kaizen.db.models import (
    ConversationHistory,
    LongTermMemory,
    ConversationSummary,
    LongTermMemory as LongTermMemoryModel,
    NotificationLog,
    NotificationPreference,
    SkillProfile,
    NotificationOutbox,
    AgentJobRun,
    RecoveryAudit,
    Player,
    Skill,
)
from kaizen.db.session import SessionLocal
from kaizen.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ConversationTurn:
    """A single turn in the conversation."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime
    token_count: int = 0


@dataclass
class MemorySnapshot:
    """Snapshot of current memory state for context."""
    recent_turns: list[ConversationTurn]
    summary: str | None
    long_term_memories: list[dict]
    player_id: int


class MemoryManager:
    """Manages conversation history, summaries, and long-term memories."""
    
    # Configuration
    MAX_HISTORY_TURNS = 20  # Before summarization
    MAX_SUMMARY_TOKENS = 1000  # Approximate token limit for summary
    SUMMARY_THRESHOLD = 15  # Trigger summarization after this many turns
    
    def __init__(self, session: Session | None = None):
        self.session = session or SessionLocal()
        self._owns_session = session is None
    
    def close(self):
        """Close the session if we own it."""
        if self._owns_session and self.session:
            self.session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ==================== Conversation History ====================
    
    def add_turn(self, player_id: int, role: str, content: str, token_count: int = 0) -> ConversationHistory:
        """Add a conversation turn to history."""
        turn = ConversationHistory(
            player_id=player_id,
            role=role,
            content=content,
            context_tokens=token_count,
            timestamp=datetime.utcnow(),
        )
        self.session.add(turn)
        self.session.flush()
        return turn
    
    def get_recent_history(self, player_id: int, limit: int = 20) -> list[ConversationHistory]:
        """Get recent conversation history for a player."""
        return (
            self.session.query(ConversationHistory)
            .filter(ConversationHistory.player_id == player_id)
            .order_by(ConversationHistory.timestamp.desc())
            .limit(limit)
            .all()
        )
    
    def get_recent_turns(self, player_id: int, limit: int = 20) -> list[ConversationTurn]:
        """Get recent turns as ConversationTurn objects."""
        history = self.get_recent_history(player_id, limit)
        return [
            ConversationTurn(
                role=h.role,
                content=h.content,
                timestamp=h.timestamp,
                token_count=h.context_tokens,
            )
            for h in reversed(history)
        ]

    # ==================== Summarization ====================
    
    def get_latest_summary(self, player_id: int) -> str | None:
        """Get the latest conversation summary for a player."""
        summary = (
            self.session.query(ConversationSummary)
            .filter(ConversationSummary.player_id == player_id)
            .order_by(ConversationSummary.created_at.desc())
            .first()
        )
        return summary.summary if summary else None
    
    def create_summary(self, player_id: int, turns: list[ConversationTurn], 
                       summary_text: str, token_count: int,
                       covered_from: datetime, covered_to: datetime) -> ConversationSummary:
        """Create a new conversation summary."""
        summary = ConversationSummary(
            player_id=player_id,
            summary=summary_text,
            token_count=len(summary_text) // 4,  # Rough token estimate
            covered_from=covered_from,
            covered_to=covered_to,
            created_at=datetime.utcnow(),
        )
        self.session.add(summary)
        self.session.flush()
        return summary
    
    async def summarize_history(self, player_id: int) -> str | None:
        """Summarize recent conversation history using AI."""
        from kaizen.ai.client import ai_client
        
        # Get recent turns not yet summarized
        latest_summary = self.get_latest_summary(player_id)
        if latest_summary:
            # Get turns after the last summary
            last_summary_time = None
            # We'd need to track when summaries were created
            pass
        
        # For now, just get recent turns
        turns = self.get_recent_turns(active_skill.player_id if False else 0, limit=30)
        if len(turns) < 5:
            return None
        
        # Build prompt for summarization
        conversation_text = "\n".join(
            f"{turn.role}: {turn.content}" for turn in turns
        )
        
        summary_prompt = (
            "Summarize the following conversation between a user and the Kaizen System. "
            "Extract key facts, preferences, goals, and patterns. "
            "Return a concise summary suitable for use as context in future interactions.\n\n"
            f"{conversation_text}"
        )
        
        from kaizen.ai.client import ai_client
        summary = ai_client._generate_text(
            prompt=conversation_text,
            system_instruction=(
                "You are the SYSTEM from Solo Leveling. Summarize the conversation "
                "concisely, extracting key facts, user preferences, goals, and patterns. "
                "Be concise and factual."
            )
        )
        
        return summary
    
    def maybe_summarize(self, player_id: int) -> bool:
        """Check if summarization is needed and perform it."""
        recent = self.get_recent_history(player_id, limit=self.MAX_HISTORY_TURNS)
        if len(recent) < self.SUMMARY_THRESHOLD:
            return False
        
        # In a real implementation, we'd call the AI to summarize
        # For now, we'll just log that summarization would happen
        logger.info(f"Summarization triggered for player {player_id}")
        return True

    # ==================== Long-term Memory ====================
    
    def add_memory(self, player_id: int, category: str, key: str, value: str,
                   confidence: float = 1.0, source: str = "ai_extraction") -> LongTermMemoryModel:
        """Add or update a long-term memory."""
        # Check for existing similar memory
        existing = (
            self.session.query(LongTermMemoryModel)
            .filter(
                LongTermMemoryModel.player_id == player_id,
                LongTermMemoryModel.category == category,
                LongTermMemoryModel.key == key,
            )
            .first()
        )
        
        if existing:
            # Update existing with higher confidence
            if confidence > existing.confidence:
                existing.value = value
                existing.confidence = confidence
                existing.source = source
                existing.updated_at = datetime.utcnow()
            return existing
        
        memory = LongTermMemoryModel(
            player_id=player_id,
            category=category,
            key=key,
            value=value,
            confidence=confidence,
            source=source,
        )
        self.session.add(memory)
        self.session.flush()
        return memory
    
    def get_memories(self, player_id: int, category: str | None = None,
                     skill_specific: bool = False) -> list[LongTermMemoryModel]:
        """Get long-term memories for a player."""
        query = self.session.query(LongTermMemoryModel).filter(
            LongTermMemoryModel.player_id == player_id
        )
        if category:
            query = query.filter(LongTermMemoryModel.category == category)
        
        return query.order_by(LongTermMemoryModel.confidence.desc()).all()
    
    def get_memories_as_dict(self, player_id: int) -> dict:
        """Get memories organized by category."""
        memories = self.get_memories(player_id)
        result = {}
        for mem in memories:
            if mem.category not in result:
                result[mem.category] = []
            result[mem.category].append({
                "key": mem.key,
                "value": mem.value,
                "confidence": mem.confidence,
                "source": mem.source,
            })
        return result
    
    def get_memory_context(self, player_id: int) -> str:
        """Get formatted memory context for AI prompts."""
        memories = self.get_memories_as_dict(player_id)
        if not memories:
            return "No long-term memories recorded yet."
        
        parts = ["LONG-TERM MEMORY:"]
        for category, items in memories.items():
            parts.append(f"\n{category.upper()}:")
            for item in items:
                parts.append(f"  - {item['key']}: {item['value']} (confidence: {item['confidence']:.1f})")
        return "\n".join(parts)

    # ==================== Memory Extraction ====================
    
    async def extract_memories(self, player_id: int, user_message: str, 
                               ai_response: str) -> list[LongTermMemoryModel]:
        """Extract long-term memories from a conversation turn."""
        from kaizen.ai.client import ai_client
        
        # Build context for extraction
        context = f"User message: {user_message}\nAssistant response: {ai_response}"
        
        prompt = (
            "Extract long-term memories from this conversation turn. "
            "Look for: user preferences, goals, habits, patterns, "
            "motivational triggers, skill targets, schedule constraints, "
            "and any other persistent facts about the user.\n\n"
            f"User: {user_message}\nAssistant: {ai_response}\n\n"
            "Return a JSON array of objects with: category, key, value, confidence (0-1).\n"
            "Categories: preference, pattern, trigger, goal, constraint, schedule."
        )
        
        result = ai_client._generate_text(
            prompt=context + "\n\n" + prompt,
            system_instruction="Extract structured long-term memories. Return only JSON array."
        )
        
        memories = []
        if result:
            try:
                memories_data = json.loads(result)
                for item in memories_data:
                    mem = self.add_memory(
                        player_id=player_id,
                        category=item.get("category", "pattern"),
                        key=item.get("key", ""),
                        value=item.get("value", ""),
                        confidence=item.get("confidence", 0.5),
                        source="ai_extraction",
                    )
                    memories.append(mem)
            except json.JSONDecodeError:
                logger.warning("Failed to parse memory extraction JSON")
        
        return memories
    
    # ==================== Complete Memory Snapshot ====================
    
    def get_memory_snapshot(self, player_id: int, skill_id: int | None = None) -> MemorySnapshot:
        """Get a complete memory snapshot for context."""
        skill = None
        if skill_id:
            from kaizen.db.models import Skill
            with SessionLocal() as db:
                skill = db.query(Skill).filter(Skill.id == skill_id).first()
        
        recent_turns = self.get_recent_turns(player_id, limit=10)
        summary = self.get_latest_summary(player_id)
        memories = self.get_memories_as_dict(player_id)
        
        return MemorySnapshot(
            recent_turns=recent_turns,
            summary=summary,
            long_term_memories=memories,
            player_id=player_id,
        )
    
    def get_context_for_ai(self, player_id: int, skill_id: int | None = None,
                           max_tokens: int = 4000) -> str:
        """Build context string for AI prompts."""
        parts = []
        
        # Add long-term memories
        memory_ctx = self.get_memory_context(player_id)
        parts.append(memory_ctx)
        
        # Add conversation summary
        summary = self.get_latest_summary(player_id)
        if summary:
            parts.append(f"\nCONVERSATION SUMMARY:\n{summary}")
        
        # Add recent turns
        turns = self.get_recent_turns(player_id, limit=8)
        if turns:
            parts.append("\nRECENT CONVERSATION:")
            for turn in turns:
                parts.append(f"{turn.role}: {turn.content}")
        
        context = "\n".join(parts)
        # Rough token limit
        if len(context) > max_tokens * 4:
            context = context[-max_tokens * 4:]
        
        return context


def get_memory_manager(session: Session | None = None) -> MemoryManager:
    """Get a memory manager instance."""
    return MemoryManager(session)


# Import at bottom to avoid circular imports
from kaizen.db.models import ConversationSummary