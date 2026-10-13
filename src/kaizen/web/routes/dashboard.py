"""FastAPI web dashboard routes for Kaizen: The System."""

from datetime import date
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from kaizen.ai.client import ai_client
from kaizen.core.player import (
    add_active_skill,
    get_or_create_player,
    remove_active_skill,
    xp_required_for_level,
)
from kaizen.core.quests import complete_quest, ensure_daily_quests, get_today_quests
from kaizen.db.models import DailyLog, Player
from kaizen.db.session import get_db

router = APIRouter()
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _get_demo_player(db: Session) -> Player | None:
    """Return the first player for this local, single-user dashboard."""
    return db.query(Player).order_by(Player.id.asc()).first()


def _dashboard_context(request: Request, db: Session, player: Player | None) -> dict:
    today = date.today()
    context = {
        "request": request,
        "player": player,
        "active_skill": None,
        "xp_needed": 100,
        "xp_pct": 0,
        "quests": [],
        "today_date": today.isoformat(),
        "all_cleared": False,
        "logs": [],
    }
    if player is None:
        return context

    skill = player.active_skill
    quests = get_today_quests(db, player.id, today)
    if skill and not quests:
        from kaizen.db.models import SkillProfile
        profile = db.query(SkillProfile).filter_by(skill_id=skill.id).first() if skill else None
        goal = profile.goal if profile else None
        target = profile.target if profile else None
        constraints = profile.constraints if profile else None
        generated = ai_client.generate_daily_quests(
            skill_name=skill.name,
            player_level=player.level,
            player_rank=player.rank,
            goal=goal,
            target=target,
            constraints=constraints,
        )
        quests = ensure_daily_quests(db, player, quests_data=generated, target_date=today)
        db.commit()

    skill = player.active_skill
    needed = xp_required_for_level(player.level)
    context.update(
        active_skill=skill,
        xp_needed=needed,
        xp_pct=min(100, round(player.current_xp / needed * 100, 1)) if needed else 0,
        quests=quests,
        all_cleared=bool(quests) and all(q.is_completed for q in quests),
        logs=(
            db.query(DailyLog)
            .filter(DailyLog.player_id == player.id)
            .order_by(DailyLog.log_date.desc())
            .limit(7)
            .all()
        ),
    )
    return context


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    player = _get_demo_player(db)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=_dashboard_context(request, db, player),
    )


@router.post("/api/register")
async def api_register(
    hunter_name: str = Form(...),
    skill_name: str = Form(...),
    db: Session = Depends(get_db),
):
    skill_name = skill_name.strip()[:128]
    if not skill_name:
        return RedirectResponse(url="/", status_code=303)

    player = _get_demo_player(db)
    if player is None:
        player, _ = get_or_create_player(
            db,
            discord_user_id="web_demo_user",
            hunter_name=hunter_name.strip()[:64] or "Hunter",
            initial_skill=skill_name,
        )
    elif player.active_skill is None:
        add_active_skill(db, player, skill_name)
    else:
        # Never silently replace a skill from a stale page submission.
        return RedirectResponse(url="/", status_code=303)

    active_skill = player.active_skill
    from kaizen.db.models import SkillProfile
    profile = None
    if active_skill:
        profile = db.query(SkillProfile).filter_by(skill_id=active_skill.id).first()
    goal = profile.goal if profile else None
    target = profile.target if profile else None
    constraints = profile.constraints if profile else None
    quests = ai_client.generate_daily_quests(
        skill_name=active_skill.name if active_skill else skill_name,
        player_level=player.level,
        player_rank=player.rank,
        goal=goal,
        target=target,
        constraints=constraints,
    )
    ensure_daily_quests(db, player, quests_data=quests)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/api/skill/remove")
async def api_remove_skill(db: Session = Depends(get_db)):
    player = _get_demo_player(db)
    if player is None:
        return RedirectResponse(url="/", status_code=303)
    try:
        remove_active_skill(db, player)
        db.commit()
    except ValueError:
        db.rollback()
    return RedirectResponse(url="/", status_code=303)


@router.post("/api/quests/{quest_id}/complete", response_class=HTMLResponse)
async def api_complete_quest(
    request: Request,
    quest_id: int,
    db: Session = Depends(get_db),
):
    player = _get_demo_player(db)
    if player is None:
        return HTMLResponse("Player not found.", status_code=404)
    try:
        complete_quest(db, player, quest_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(escape(str(exc)), status_code=400)

    context = _dashboard_context(request, db, player)
    return templates.TemplateResponse(
        request=request,
        name="partials/quests_list.html",
        context=context,
    )


@router.post("/api/log", response_class=HTMLResponse)
async def api_log_training(
    notes: str = Form(...),
    db: Session = Depends(get_db),
):
    player = _get_demo_player(db)
    if player is None:
        return HTMLResponse("Player not found.", status_code=404)
    today = date.today()
    quests = get_today_quests(db, player.id, today)
    completed = sum(q.is_completed for q in quests)
    skill = player.active_skill
    feedback = ai_client.analyze_log(
        skill_name=skill.name if skill else "Mastery",
        user_notes=notes,
        quests_completed=completed,
        quests_total=len(quests),
    ) or "The System has recorded your training data. Continue your progression."
    log = (
        db.query(DailyLog)
        .filter(DailyLog.player_id == player.id, DailyLog.log_date == today)
        .first()
    )
    if log is None:
        log = DailyLog(player_id=player.id, log_date=today)
        db.add(log)
    log.user_notes = notes[:4000]
    log.ai_feedback = feedback[:4000]
    log.quests_total = len(quests)
    log.quests_completed = completed
    db.commit()
    return HTMLResponse(
        f"<div class='ai-bubble'><strong>System Evaluation:</strong> {escape(feedback)}</div>"
    )


@router.post("/api/coach", response_class=HTMLResponse)
async def api_coach_chat(
    message: str = Form(...),
    db: Session = Depends(get_db),
):
    player = _get_demo_player(db)
    if player is None:
        return HTMLResponse("Player not found.", status_code=404)
    skill = player.active_skill
    context = (
        f"Hunter: {player.hunter_name}, Rank: {player.rank}, Level: {player.level}, "
        f"Skill: {skill.name if skill else 'None'} "
        f"({skill.mastery_pct if skill else 0}% mastery), Streak: {player.current_streak} days"
    )
    reply = ai_client.chat(player_context=context, user_message=message) or (
        "The System is currently unreachable. Trust your training."
    )
    return HTMLResponse(
        "<div class='ai-bubble' style='border-left-color: var(--border-cyan);'>"
        f"<strong>System:</strong> {escape(reply)}</div>"
    )
