# Read the file
with open('E:/Personal_Projects/Kaizen/src/kaizen/core/notifications.py', 'r') as f:
    content = f.read()

# Find the method boundaries
old_method_start = content.find('async def schedule_daily_notifications(self, player_id: int) -> list:')
old_method_end = content.find('    async def process_outbox')

if old_method_start == -1 or old_method_end == -1:
    print('Could not find method boundaries')
    exit(1)

# New method with correct indentation
new_method = '''    async def schedule_daily_notifications(self, player_id: int) -> list:
        """Generate and queue notifications for all active skills for today."""
        with SessionLocal() as db:
            from kaizen.db.models import Skill, SkillProfile, Player

            # Get player
            player = db.query(Player).filter(Player.id == player_id).first()
            if not player:
                return []

            # Get all active skills with profiles
            skills = (
                db.query(Skill)
                .join(SkillProfile, Skill.id == SkillProfile.skill_id)
                .filter(Skill.player_id == player_id)
                .filter(SkillProfile.confirmed == True)
                .all()
            )

            if not skills:
                return []

            today = datetime.now(timezone.utc).date()
            results = []

            for skill in skills:
                plan = await self.generate_notification_plan(player.id, skill.id, datetime.now())
                if not plan:
                    continue

                for notif in plan:
                    outbox = NotificationOutbox(
                        player_id=player.id,
                        skill_id=skill.id,
                        scheduled_at=notif["time_utc"],
                        message=notif["message"],
                        notification_type=notif["type"],
                        idempotency_key=f"{player.id}_{skill.id}_{notif['type']}_{notif['time_utc'].date()}",
                    )
                    db.add(outbox)
                    results.append(outbox)

            db.commit()
            return results

'''

# Replace the old method with the new one
new_content = content[:old_method_start] + new_method + content[old_method_end:]

with open('E:/Personal_Projects/Kaizen/src/kaizen/core/notifications.py', 'w') as f:
    f.write(new_content)

print('Fixed')