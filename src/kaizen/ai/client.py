"""FreeLLM AI client for Kaizen: The System.

Provides:
- Daily quest generation (AI-driven, with template fallback)
- AI coaching feedback on player logs
- Free-form "System" chat

Uses a custom FreeLLM-compatible API. Falls back gracefully when API is unavailable.
"""

import json
import logging
import re
from typing import Any

from kaizen.config import settings

logger = logging.getLogger(__name__)


class AIClient:
    """OpenAI-compatible client for the local FreeLLM server."""

    def __init__(self) -> None:
        self._client: Any = None
        self._api_key = settings.API_KEY.strip()
        self._base_url = settings.BASE_URL.strip().rstrip("/")
        self._configured_model = settings.MODEL.strip()
        self._resolved_model: str | None = None
        self._models_checked = False
        if self._configured_model.lower() == "custom-freellm":
            self._configured_model = ""
        self._resolved_model: str | None = None
        self._models_checked = False

        if not self._api_key:
            logger.warning("API_KEY is not configured; AI features will use fallbacks.")
            return

        try:
            import requests

            self._client = requests
            logger.info("FreeLLM client configured for %s", self._base_url)
        except ImportError:
            logger.warning("requests is not installed; AI features will use fallbacks.")

    @property
    def is_available(self) -> bool:
        return self._client is not None and bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _get_model(self) -> str | None:
        """Choose a configured model or discover the first model served locally."""
        if self._models_checked:
            return self._resolved_model

        self._models_checked = True
        try:
            response = self._client.get(
                f"{self._base_url}/models",
                headers=self._headers(),
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Cannot reach FreeLLM model list at %s: %s", self._base_url, exc)
            return self._configured_model or None

        if response.status_code == 401:
            logger.error("FreeLLM rejected API_KEY (401). Set API_KEY to a valid key from the local server.")
            return None
        if response.status_code >= 400:
            logger.warning("FreeLLM /models returned HTTP %s", response.status_code)
            return self._configured_model or None

        try:
            data = response.json()
            models = data.get("data", data.get("models", []))
            model_ids = [
                item.get("id") or item.get("name")
                for item in models
                if isinstance(item, dict) and (item.get("id") or item.get("name"))
            ]
        except (ValueError, AttributeError, TypeError):
            model_ids = []

        if self._configured_model and self._configured_model in model_ids:
            self._resolved_model = self._configured_model
        elif model_ids:
            self._resolved_model = model_ids[0]
            if self._configured_model:
                logger.warning(
                    "Configured MODEL '%s' is not served locally; using '%s'.",
                    self._configured_model,
                    self._resolved_model,
                )
            else:
                logger.info("Using first local FreeLLM model: %s", self._resolved_model)
        else:
            self._resolved_model = self._configured_model or None
            if self._resolved_model is None:
                logger.error("FreeLLM returned no model IDs and MODEL is not configured.")

        return self._resolved_model

    def _generate_text(self, prompt: str, system_instruction: str | None = None) -> str | None:
        """Generate a text response from the LLM. Returns None on failure."""
        if not self.is_available:
            return None

        try:
            model = self._get_model()
            if not model:
                return None
            payload = {
                "model": model,
                "messages": ([{"role": "system", "content": system_instruction}]
                             if system_instruction else [])
                + [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 800,
            }

            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=30,
            )

            if response.status_code == 401:
                logger.error("FreeLLM rejected API_KEY (401). Update API_KEY with a valid local-server key.")
                return None
            if response.status_code == 404:
                logger.error(
                    "FreeLLM returned 404 for chat/completions; check BASE_URL and the model list. Response: %s",
                    response.text[:240],
                )
                return None
            if response.status_code != 200:
                logger.warning("FreeLLM chat/completions returned HTTP %s", response.status_code)
                return None

            data = response.json()
            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content")
            return content if isinstance(content, str) and content.strip() else None

        except Exception as exc:
            logger.warning("FreeLLM request failed; using fallback behavior: %s", exc)
            return None

    def generate_daily_quests(
        self,
        skill_name: str,
        player_level: int,
        player_rank: str,
        recent_notes: str | None = None,
        goal: str | None = None,
        target: str | None = None,
        constraints: str | None = None,
    ) -> list[dict] | None:
        """Generate 3-4 AI-driven daily quests for the player.

        Returns a list of quest dicts, or None if AI is unavailable.
        """
        if not self.is_available:
            return None

        system_instruction = ("""
            You are the Quest Generation Engine for Kaizen, a gamified personal
            development system inspired by RPG progression mechanics.

            PERSONALITY:
            Use a concise, disciplined, authoritative system voice.
            Quest titles should be immersive and memorable, but descriptions must
            remain practical, realistic, unambiguous, and achievable in real life.
            Do not sacrifice logic or usability for dramatic language.

            CRITICAL ALIGNMENT:
            Quests MUST be aligned with the hunter's stated GOAL and TARGET.
            If the TARGET specifies a precise time, value, or outcome, every quest
            must support that exact target. Never generate contradictory quests.
            For example, if TARGET is "Wake up at 6:30 AM IST", do NOT generate a
            quest to wake at 6:00 AM or any other time. All quests should enable
            the hunter to achieve the stated target.

            YOUR TASK:
            Generate exactly 3 distinct daily quests for the single skill provided
            by the user. The quests must help the hunter improve that skill through
            different, complementary behaviors.

            MANDATORY QUEST DESIGN RULES:

            1. DISTINCT OBJECTIVES
            - Each quest must have a different primary objective and target behavior.
            - Do not create three versions of the same task by changing its time,
            duration, intensity, quantity, or difficulty.
            - Do not reuse the same core action across multiple quests.
            - Each quest must provide a distinct contribution to the target skill.
            - For example, for "Wakeup early", use different approaches such as
            morning execution, evening preparation, and schedule consistency.
            Do NOT generate three increasingly early wake-up times.

            2. INDEPENDENT COMPLETION
            - Every quest must be completable independently of the other two.
            - Never require another quest to be completed first.
            - Do not make one quest a prerequisite for another.
            - Do not award completion for an action that merely forms part of
            another quest's required action.
            - Avoid overlapping time windows, duplicated deliverables, and
            compound objectives that bundle several unrelated tasks.
            - Each quest must have its own clearly verifiable completion condition.

            3. SPECIFIC AND MEASURABLE
            - Describe exactly what the hunter must do and the condition for success.
            - Include measurable quantities, durations, deadlines, or observable
            outcomes whenever appropriate.
            - Avoid vague instructions such as "be more disciplined",
            "improve your mindset", or "work harder".
            - A quest must be understandable without additional explanation.
            - Prefer one primary objective per quest. Supporting steps are allowed
            only when they are essential to that objective.

            4. REALISTIC AND SAFE
            - Design quests for completion within a single day.
            - Consider the hunter's current level, rank, and recent training notes.
            - Do not assume access to special equipment, money, or specific locations.
            - Do not prescribe extreme sleep deprivation, excessive exercise,
            unsafe diets, or other harmful practices.
            - Do not make success depend on uncontrollable external events.
            - Prefer sustainable progress over arbitrary increases in intensity.

            5. PROGRESSION AND DIFFICULTY
            - Arrange the three quests in ascending order of overall challenge:
            order_index 1 = introductory, 2 = intermediate, 3 = advanced.
            - Increase challenge through meaningful differences in effort,
            precision, consistency, planning, or execution—not by duplicating tasks.
            - All quests must remain independently completable.
            - Use the hunter's level and rank to calibrate difficulty.
            - Do not automatically assign E, D, C to the three quests.
            - The difficulty field represents the actual challenge of that quest.
            - Valid difficulty values: E, D, C, B, A, S.
            - Do not assign an S-rank quest simply to make the final quest harder.
            - Higher-level hunters should receive more sophisticated challenges,
            not merely larger quantities of the same action.

            6. SKILL RELEVANCE
            - Every quest must directly improve the specified skill.
            - Do not introduce unrelated activities just to create variety.
            - Interpret the skill semantically, considering its practical components.
            - If the skill is broad, select complementary subskills.
            - If the skill is narrow, find distinct ways to practice or reinforce it
            without repeating the same primary objective.

            7. REWARDS
            - xp_reward must be an integer between 30 and 200 inclusive.
            - Assign higher rewards to quests with greater legitimate challenge.
            - Reward differences must reflect effort, complexity, or sustained
            discipline rather than arbitrary rank labels.
            - The rewards for the three quests should normally increase with
            order_index.

            8. USE OF RECENT NOTES
            - Treat recent training notes as historical context, not as mandatory
            tasks to repeat.
            - Avoid repeating recently completed activities unless deliberate
            repetition is necessary for the skill.
            - If the hunter reports difficulty, adjust the next challenges
            appropriately instead of automatically increasing difficulty.
            - Never invent accomplishments or assume a quest was completed.

            9. TITLES AND DESCRIPTIONS
            - Titles should be short, distinctive, and related to the objective.
            - Descriptions should normally be one or two concise sentences.
            - Use a cold, authoritative RPG system tone without excessive theatrics.
            - Do not include markdown, emojis, XP text, rank labels, warnings,
            or formatting inside the description.
            - Do not repeat the title in the description.

            10. FINAL QUALITY CHECK
            Before returning the result, silently verify:
            - Exactly three quests exist.
            - Each quest targets a distinct primary behavior.
            - No two quests are duplicates or stricter versions of each other.
            - Each quest can be completed without completing another quest.
            - Each completion condition is clear and independently verifiable.
            - All quests are realistic, safe, and relevant to the specified skill.
            - Difficulty and XP rewards reflect genuine progression.
            - All required fields have the correct types and values.

            If any quest fails these checks, redesign it before returning the result.

            OUTPUT CONTRACT:
            Return ONLY a valid JSON array containing exactly three objects.
            Every object must contain exactly these keys:
            title, description, difficulty, xp_reward, order_index.

            Types:
            - title: string
            - description: string
            - difficulty: one of "E", "D", "C", "B", "A", "S"
            - xp_reward: integer from 30 to 200
            - order_index: integer, exactly 1, 2, and 3 respectively

            No markdown, no commentary, no code fences, and no additional keys.
            """
        )

        
        prompt = f"""
            Generate exactly 3 daily quests for the following hunter.

            SKILL: {skill_name}
            HUNTER LEVEL: {player_level}
            HUNTER RANK: {player_rank}
            GOAL: {goal or 'Improve skill'}
            TARGET: {target or 'Reach mastery'}
            CONSTRAINTS: {constraints or 'None'}

        """

        if recent_notes:
            prompt += f"""
            RECENT TRAINING NOTES:
            {recent_notes}

            Use these notes to avoid unnecessary repetition and calibrate the
            challenge appropriately. Do not assume that an activity was completed
            unless the notes explicitly say so.
            """

        prompt += """
            GENERATION REQUIREMENTS:

            First, identify three different behavioral dimensions of the specified
            skill. Use those dimensions to create complementary quests.

            The quests MUST BE ALIGNED with GOAL and TARGET. Do NOT contradict the target time/value.
            If TARGET is to wake up at 6:30 AM, never generate quests to wake at 6:00 AM or different time.

            For each quest, define:
            1. One distinct primary objective.
            2. A concrete, independently verifiable success condition.
            3. A realistic challenge appropriate for this hunter.
            4. A difficulty rank and XP reward reflecting the actual challenge.

            The three quests must be independently completable. Completing one
            must not automatically complete another. Do not create three variations
            of the same action by changing its time, duration, intensity, or quantity.

            Order the quests from lower to higher overall challenge, while preserving
            distinct objectives. Do not force the difficulty ranks to be consecutive.

            Return only the JSON array specified in your system instructions.
            """


        text = self._generate_text(prompt, system_instruction)
        if not text:
            return None

        # Strip any markdown code blocks
        cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        try:
            quests = json.loads(cleaned)
            if isinstance(quests, list) and all(isinstance(q, dict) for q in quests):
                for i, q in enumerate(quests):
                    q.setdefault("order_index", i + 1)
                    q.setdefault("source", "ai")
                return quests
        except json.JSONDecodeError:
            logger.warning("FreeLLM returned non-JSON quest data: %s", cleaned[:200])
            return None

        return None

    def analyze_log(
        self,
        skill_name: str,
        user_notes: str,
        quests_completed: int,
        quests_total: int,
    ) -> str | None:
        """Analyze a player's daily log and return AI coaching feedback."""
        if not self.is_available:
            return None

        system_instruction = (
            "You are the SYSTEM from Solo Leveling. Analyze the hunter's daily log and provide "
            "concise, constructive coaching feedback. Be cold, analytical, and encouraging in a "
            "professional tone. Keep it under 200 words."
        )

        prompt = (
            f"Skill: {skill_name}\n"
            f"Quests Completed: {quests_completed}/{quests_total}\n"
            f"Hunter's Notes: {user_notes}\n"
            "Provide feedback on what went well, what to improve, and one actionable suggestion for tomorrow."
        )

        return self._generate_text(prompt, system_instruction)

    def chat(self, player_context: str, user_message: str) -> str | None:
        """Free-form chat with the SYSTEM."""
        if not self.is_available:
            return None

        system_instruction = (
            "You are the SYSTEM from Solo Leveling. A cold, mechanical, slightly ominous AI entity "
            "that monitors the hunter's progress. Respond in character — brief, analytical, "
            "and slightly intimidating. You may occasionally mock failures. Never break character."
        )

        prompt = f"Hunter Profile:\n{player_context}\n\nHunter: {user_message}"
        return self._generate_text(prompt, system_instruction)

    def chat_completion_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Return the assistant message from an OpenAI-compatible tool call request."""
        if not self.is_available:
            return None

        try:
            model = self._get_model()
            if not model:
                return None
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "temperature": 0.2,
                    "max_tokens": 800,
                },
                timeout=30,
            )
            if response.status_code != 200:
                logger.warning(
                    "FreeLLM tool chat returned HTTP %s: %s",
                    response.status_code,
                    response.text[:240],
                )
                return None
            choices = response.json().get("choices", [])
            if not choices or not isinstance(choices[0].get("message"), dict):
                return None
            return choices[0]["message"]
        except Exception as exc:
            logger.warning("FreeLLM tool chat failed: %s", exc)
            return None


ai_client = AIClient()
