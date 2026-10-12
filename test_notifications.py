from kaizen.db.session import SessionLocal
from kaizen.db.models import Player, Skill, SkillProfile
from kaizen.core.notifications import get_notification_manager

with SessionLocal() as db:
    players = db.query(Player).all()
    print(f'Found {len(players)} players')
    for p in players:
        print(f"Player: {p.hunter_name}, Discord ID: {p.discord_user_id}")
        if p.active_skill:
            print(f"  Active skill: {p.active_skill.name}")
            profile = db.query(SkillProfile).filter(SkillProfile.skill_id == p.active_skill.id).first()
            if profile:
                print(f"  Profile exists: goal={profile.goal}, confirmed={profile.confirmed}")
            else:
                print(f"  No profile exists")
        else:
            print(f"  No active skill")
