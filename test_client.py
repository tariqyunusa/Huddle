import asyncio
import json
import sys
import websockets

async def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "Anonymous"
    uri = f"ws://localhost:8001/ws/session/a885da89-8c89-4ddd-ab25-c306d0ade719?display_name={name}"
    async with websockets.connect(uri) as ws:
        print(f"Connected as {name}. Type messages, or 'quit' to exit.")

        async def listen():
            async for message in ws:
                data = json.loads(message)
                print(f"\n[{data['author']}]: {data['content']}\n> ", end="")

        listener_task = asyncio.create_task(listen())

        loop = asyncio.get_event_loop()
        while True:
            msg = await loop.run_in_executor(None, input, "> ")
            if msg.strip().lower() == "quit":
                break
            await ws.send(json.dumps({"content": msg}))

        listener_task.cancel()

asyncio.run(main())