"""Unified entrypoint to run both Discord bot and FastAPI dashboard."""

import asyncio
import logging
import signal
import sys

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from kaizen.ai.client import ai_client
from kaizen.bot.bot import KaizenBot
from kaizen.config import settings
from kaizen.core.agent import get_agent_manager
from kaizen.core.progress import check_and_apply_penalty
from kaizen.db.models import Player
from kaizen.db.session import SessionLocal
from kaizen.web.app import app as fastapi_app

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


async def run_dashboard() -> None:
    """Run the FastAPI dashboard using Uvicorn."""
    config = uvicorn.Config(
        fastapi_app,
        host=settings.WEB_HOST,
        port=settings.WEB_PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_bot() -> None:
    """Run the Discord bot."""
    bot = KaizenBot()
    await bot.start(settings.DISCORD_TOKEN)


async def daily_reset_job() -> None:
    """At midnight, reset daily quests for all active players."""
    logger.info("Running daily reset job: generating new quests for all players.")
    with SessionLocal() as db:
        players = db.query(Player).all()
        for player in players:
            # Skip if no active skill
            if not player.active_skill:
                continue
            # Generate new quests for today (overwrites any existing)
            skill = player.active_skill
            skill_name = skill.name
            # Load profile for alignment
            from kaizen.db.models import SkillProfile
            profile = db.query(SkillProfile).filter_by(skill_id=skill.id).first()
            goal = profile.goal if profile else None
            target = profile.target if profile else None
            constraints = profile.constraints if profile else None
            # Run synchronous AI call in thread pool to avoid blocking event loop
            ai_quests = await asyncio.to_thread(
                ai_client.generate_daily_quests,
                skill_name=skill_name,
                player_level=player.level,
                player_rank=player.rank,
                goal=goal,
                target=target,
                constraints=constraints,
            )
            # Remove old quests for today
            db.query(
                __import__("kaizen.db.models", fromlist=["Quest"]).Quest
            ).filter(
                __import__("kaizen.db.models", fromlist=["Quest"]).Quest.player_id == player.id,
                __import__("kaizen.db.models", fromlist=["Quest"]).Quest.quest_date == __import__("datetime").date.today(),
            ).delete(synchronize_session=False)

            # Ensure new quests
            from kaizen.core.quests import ensure_daily_quests

            ensure_daily_quests(db, player, quests_data=ai_quests)
        db.commit()
    logger.info("Daily reset job completed.")


async def penalty_check_job() -> None:
    """Check for penalties (missed days) for all players at a set hour."""
    logger.info("Running penalty check job.")
    with SessionLocal() as db:
        players = db.query(Player).all()
        for player in players:
            from kaizen.core.progress import check_and_apply_penalty

            penalty_res = check_and_apply_penalty(db, player)
            if penalty_res.penalty_incurred:
                logger.info(
                    "Penalty applied for player %s: %s",
                    player.discord_user_id,
                    penalty_res.message,
                )
        db.commit()
    logger.info("Penalty check job completed.")


async def reminder_job() -> None:
    """Run the agent's reminder job: generate daily notifications."""
    logger.info("Running reminder job: generating daily notifications.")
    with get_agent_manager() as agent_mgr:
        result = await agent_mgr.run_reminder_job()
    logger.info(f"Reminder job completed: {result}")


async def process_outbox_job() -> None:
    """Process notification outbox frequently to deliver on time."""
    with get_agent_manager() as agent_mgr:
        sent = await agent_mgr.process_notification_outbox()
    if sent:
        logger.info(f"Outbox processor sent {sent} notifications")


def setup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    # Daily quest generation at midnight
    scheduler.add_job(daily_reset_job, "cron", hour=settings.DAILY_RESET_HOUR, minute=0)
    # Penalty check in the morning
    scheduler.add_job(
        penalty_check_job, "cron", hour=settings.DAILY_REMINDER_HOUR, minute=0
    )
    # Daily notification generation at 9am
    scheduler.add_job(reminder_job, "cron", hour=9, minute=0)
    # Frequent outbox processing (every minute)
    scheduler.add_job(process_outbox_job, "interval", minutes=1)
    return scheduler


async def main() -> None:
    setup_logging()
    logger.info("Starting Kaizen: The System...")

    scheduler = setup_scheduler()
    scheduler.start()

    # Run bot and dashboard concurrently
    await asyncio.gather(run_bot(), run_dashboard())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        sys.exit(1)
