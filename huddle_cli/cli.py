"""
Huddle CLI — join or create group reasoning sessions from the terminal.
"""
import asyncio
import json

import click
import httpx
import websockets


@click.group()
def main():
    """Huddle — group reasoning sessions from your terminal."""
    pass


@main.command()
@click.option("--host", default="localhost", help="Server host (e.g. 192.168.1.42)")
@click.option("--port", default=8001, help="Server port")
@click.option("--title", default=None, help="Session title")
@click.option("--created-by", required=True, help="Your user UUID")
def create(host, port, title, created_by):
    """Create a new session and print its ID."""
    url = f"http://{host}:{port}/sessions"
    response = httpx.post(url, json={"title": title, "created_by": created_by})
    response.raise_for_status()
    data = response.json()
    click.echo(f"Session created: {data['id']}")
    click.echo(f"Share this to invite others:\n  huddle join {data['id']} --host {host} --name <their-name>")


@main.command()
@click.argument("session_id")
@click.option("--host", default="localhost", help="Server host (e.g. 192.168.1.42)")
@click.option("--port", default=8001, help="Server port")
@click.option("--name", default="Anonymous", help="Your display name")
def join(session_id, host, port, name):
    """Join an existing session by its ID."""
    asyncio.run(_join(session_id, host, port, name))


async def _join(session_id, host, port, name):
    uri = f"ws://{host}:{port}/ws/session/{session_id}?display_name={name}"

    async with websockets.connect(uri) as ws:
        click.echo(f"Connected as {name}. Type messages, or 'quit' to exit.")

        async def listen():
            async for message in ws:
                data = json.loads(message)
                msg_type = data.get("type")
                if msg_type == "thinking":
                    print("\n[...thinking...]\n> ", end="")
                elif msg_type == "error":
                    print(f"\n[Error]: {data.get('content')}\n> ", end="")
                else:
                    print(f"\n[{data.get('author', 'Unknown')}]: {data.get('content', '')}\n> ", end="")

        listener_task = asyncio.create_task(listen())

        loop = asyncio.get_event_loop()
        while True:
            msg = await loop.run_in_executor(None, input, "> ")
            if msg.strip().lower() == "quit":
                break
            await ws.send(json.dumps({"content": msg}))

        listener_task.cancel()


if __name__ == "__main__":
    main()