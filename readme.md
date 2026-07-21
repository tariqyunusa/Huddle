# Huddle

Group reasoning sessions — multiple people, one shared conversation, one AI (Talon) in the loop.

## What this is

Huddle lets 2+ people join the same live session from separate machines and prompt an AI together. Everyone sees every prompt and every reply, in order, as it happens. Prompts are turn-locked (one AI response is generated at a time, even if multiple people send messages at once) and the full transcript persists, so late joiners see the history.

## Prerequisites (host machine only)

- Docker Desktop
- Python 3.12+ and a virtual environment
- An API key for the AI provider currently wired in (Groq, free tier — see `.env.example`)

## 1. Start the backend (host machine)

From the project root:

```bash
docker compose up -d
docker compose ps
```

You should see three containers running: `huddle-api-1`, `huddle-db-1`, `huddle-redis-1`.

First time only — apply database migrations:

```bash
alembic upgrade head
```

## 2. Install the CLI (host machine)

```bash
pip install -e .
```

This installs the `huddle` command locally.

## 3. Create a user (one-time, host machine)

Huddle doesn't have signup yet — for now, insert a user row directly:

```bash
docker exec -it huddle-db-1 psql -U huddle -d huddle -c \
  "INSERT INTO users (id, email, display_name, created_at) VALUES (gen_random_uuid(), 'you@example.com', 'Your Name', now()) RETURNING id;"
```

Copy the returned `id` — you'll use it as `--created-by` when creating sessions.

## 4. Create a session (host machine)

```bash
huddle create --created-by <your-user-id> --title "My session"
```

This prints a session ID and a ready-to-share join command, e.g.:

```
Session created: 0a2f00d0-6215-4944-bea0-d4f464f3e52e
Share this to invite others:
  huddle join 0a2f00d0-6215-4944-bea0-d4f464f3e52e --host localhost --name <their-name>
```

## 5. Find your LAN IP (host machine)

Others on your network need this to reach you.

```bash
ipconfig        # Windows
ifconfig        # Mac/Linux
```

Note the IPv4 address (e.g. `192.168.1.42`).

## 6. Allow inbound connections on port 8001 (host machine, one-time)

**Windows (PowerShell, run as Administrator):**

```powershell
New-NetFirewallRule -DisplayName "Huddle Dev" -Direction Inbound -LocalPort 8001 -Protocol TCP -Action Allow
```

## 7. Join the session — host

```bash
huddle join <session-id> --name Alice
```

## 8. Join the session — everyone else

No cloning, no install beyond `uv`. On any other machine (same network, or public internet once deployed):

**Install `uv` (one-time):**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # Mac/Linux
```

```powershell
irm https://astral.sh/uv/install.ps1 | iex          # Windows
```

**Join:**

```bash
uvx --from git+https://github.com/tariqyunusa/Huddle.git huddle join <session-id> --host <host-LAN-IP> --name <your-name>
```

## Notes

- Everyone must be on the same local network for now (LAN IP based). Once deployed publicly, `--host` becomes a public domain and no firewall/network setup is needed — see roadmap below.
- All AI replies currently come from Talon, running on Groq's free-tier Llama model.
- Sessions and messages persist in Postgres — reconnecting to the same session ID replays the full history.

## Roadmap

- [ ] Public deployment (Render) — removes LAN/firewall requirement entirely
- [ ] Real authentication (replace manual user-row creation)
- [ ] Multi-provider support — host picks Anthropic/Groq/other + model per session
- [ ] Web UI (in addition to the CLI)