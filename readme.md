# Huddle

Group reasoning sessions — multiple people, one shared conversation, one AI (**Talon**) in the loop.

## What this is

Huddle lets 2+ people join the same live session from separate machines and prompt an AI together. Everyone sees every prompt and every reply, in order, as it happens. Prompts are turn-locked (one AI response is generated at a time, even if multiple people send messages at once) and the full transcript persists, so late joiners see the history.

Huddle is hosted — there's no local setup required to use it. The only prerequisite is having `uv` installed.

## Using Huddle (no local setup required)

### 1. Install `uv` (one-time, per machine)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # Mac/Linux
```

```powershell
irm https://astral.sh/uv/install.ps1 | iex          # Windows
```

Confirm it installed:
```bash
uv --version
```

### 2. Sign up

```bash
uvx --from git+https://github.com/tariqyunusa/Huddle.git huddle signup --host huddle-6j42.onrender.com --email you@example.com --name "Your Name"
```

This prints a user ID — save it, you'll need it to create sessions.

### 3. Create a session

```bash
uvx --from git+https://github.com/tariqyunusa/Huddle.git huddle create --host huddle-6j42.onrender.com --created-by <your-user-id> --title "My session"
```

This prints a session ID and a ready-to-share join command.

### 4. Join a session

Anyone with the session ID can join, from anywhere:

```bash
uvx --from git+https://github.com/tariqyunusa/Huddle.git huddle join <session-id> --host huddle-6j42.onrender.com --name <your-name>
```

Type a message and press enter to send. Type `quit` to leave. Everyone in the session sees every prompt, every reply from Talon, and the full history if they join late.

## Notes

- All AI replies currently come from **Talon**, running on Groq's free-tier Llama model.
- Sessions and messages persist in Postgres (Neon) — reconnecting to the same session ID replays the full history.
- The free Render instance may take 30-60 seconds to "wake up" if it's been idle — the first command after a period of inactivity may time out or feel slow. Just retry.

## Roadmap

- [ ] Real authentication (replace manual signup with proper login/session tokens)
- [ ] Multi-provider support — host picks Anthropic/Groq/other + model per session
- [ ] Web UI (in addition to the CLI)
- [ ] Custom domain / nicer session-sharing links

---

## For contributors — local development setup

### Prerequisites

- Docker Desktop
- Python 3.12+ and a virtual environment
- A Groq API key (free tier — see `.env.example`)

### 1. Start the backend

```bash
docker compose up -d
docker compose ps
```

You should see three containers running: `huddle-api-1`, `huddle-db-1`, `huddle-redis-1`.

First time only — apply database migrations:

```bash
alembic upgrade head
```

### 2. Install the CLI locally (editable)

```bash
pip install -e .
```

This installs the `huddle` command into your local venv, pointing at `localhost` by default.

### 3. Create a user (local dev only — no signup endpoint needed if testing directly against Postgres)

```bash
huddle signup --email you@example.com --name "Your Name"
```

### 4. Create and join a session locally

```bash
huddle create --created-by <your-user-id> --title "Local test"
huddle join <session-id> --name Alice
```

### Environment variables

| Variable | Local value | Production value |
|---|---|---|
| `DATABASE_URL` | `postgresql://huddle:huddle_dev_pw@db:5432/huddle` | Neon pooled connection string |
| `REDIS_URL` | `redis://redis:6379` | Render Key Value internal URL |
| `GROQ_API_KEY` | your Groq key | your Groq key |