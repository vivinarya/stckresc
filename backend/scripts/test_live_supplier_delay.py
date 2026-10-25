"""Test live cloud-hosted stockrescue-coordinator with the natural language supplier delay prompt."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    MetadataContent,
    chat_protocol_spec,
)

TARGET_COORDINATOR = "agent1qwftafhh6kekpkzqxh4mdzuvnphhqrqkwxfxgnaccde3fms6r65svnk0cma"
PROMPT = (
    "Our main supplier just told us that a delivery will be delayed by 3 days. "
    "We have two production jobs waiting, and I'm worried we'll miss our deadlines. "
    "Can you check our available inventory, identify which jobs are at risk, and "
    "find suitable alternative suppliers? Please explain what you find in simple terms."
)

test_client = Agent(name="stockrescue-tester-delay", port=8098)
chat_proto = Protocol(spec=chat_protocol_spec)

received_reply = asyncio.Event()


@chat_proto.on_message(ChatMessage)
async def handle_reply(ctx: Context, sender: str, msg: ChatMessage):
    print(f"\n=======================================================")
    print(f"[RECEIVED LIVE CHAT REPLY FROM COORDINATOR ({sender})]")
    print(f"=======================================================")
    for item in msg.content:
        if isinstance(item, TextContent):
            print(f"Text Reply:\n{item.text}\n")
        elif isinstance(item, MetadataContent):
            print(f"Attached Card Metadata: {item.metadata}\n")
        else:
            print(f"Content Item: {item}")
    received_reply.set()


@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    print(f"[ACK RECEIVED] Msg ID: {msg.acknowledged_msg_id}")


test_client.include(chat_proto)


@test_client.on_event("startup")
async def send_test_prompt(ctx: Context):
    print(f"Sending prompt to live coordinator at {TARGET_COORDINATOR}...")
    msg_id = uuid4()
    await ctx.send(
        TARGET_COORDINATOR,
        ChatMessage(
            timestamp=datetime.now(timezone.utc),
            msg_id=msg_id,
            content=[TextContent(type="text", text=PROMPT)],
        ),
    )


async def main():
    loop = asyncio.get_event_loop()
    run_task = loop.create_task(test_client.run_async())

    try:
        await asyncio.wait_for(received_reply.wait(), timeout=35.0)
        print("\nSUCCESS: Live coordinator processed supplier delay query and returned grounded response!")
    except asyncio.TimeoutError:
        print("\nTest timed out waiting for reply envelope from coordinator.")
    finally:
        test_client.server.should_exit = True


if __name__ == "__main__":
    asyncio.run(main())
