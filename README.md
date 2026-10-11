# Kaizen: The System

> **"The System demands continuous progression. Failure to complete daily quests will result in entering the Penalty Zone."**

A **Solo Leveling**-inspired skill improvement tracker with **1% daily progression**, **XP & levels**, **daily quests**, and **AI coaching**.

## Features

✅ **Daily 1% Mastery** — Complete all quests to gain +1% skill mastery
✅ **Solo Leveling Aesthetic** — Blue hologram UI, rank system (E→S), penalty zone
✅ **AI Coach (local FreeLLM)** — Generates adaptive daily quests, analyzes logs, and provides feedback
✅ **Discord Bot** — Slash commands for quests, status, and logging
✅ **Web Dashboard** — Character sheet, quest checklist, and history
✅ **Gamification** — XP, levels, streaks, rank promotions, and penalty system

## Setup

1. **Clone the repo**
   ```sh
   git clone https://github.com/yourusername/kaizen.git
   cd kaizen
   ```

2. **Install dependencies**
   ```sh
   python -m venv .venv
   .\.venv\Scripts\activate  # Windows
   source .venv/bin/activate  # Linux/Mac
   pip install -r requirements.txt
   ```

3. **Configure `.env`**
   Copy `.env.example` to `.env` and fill in your credentials:
   ```sh
   DISCORD_TOKEN="your_discord_bot_token"
   BASE_URL="http://localhost:3001/v1"
   API_KEY="your_local_freellm_api_key"
   MODEL=""  # optional; empty selects the first model from /v1/models
   ```

4. **Run the System**
   ```sh
   python -m kaizen
   ```

## Commands

| Command | Description |
|---------|-------------|
| `/register skill:<name>` | Awaken as a Player and choose your skill to grind |
| `/remove-skill` | Remove your active skill after confirming the removal |
| `/quests` | View today's Daily Quests (interactive buttons) |
| `/complete <quest_id>` | Mark a quest as completed |
| `/status` | Inspect your character status and progress |
| `/log <notes>` | Record training notes and receive AI feedback |
| `/coach <message>` | Chat with the System (AI) |

## Web Dashboard

After starting the bot, the web dashboard will be available at:

🌐 [http://127.0.0.1:8001](http://127.0.0.1:8001)

## License

MIT

## Credits

Inspired by **Solo Leveling** (Webtoon) and the **1% rule** for continuous improvement.

> **"The System does not forgive weakness. Train. Evolve. Repeat."**
