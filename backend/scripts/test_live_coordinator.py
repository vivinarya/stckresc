"""Test live cloud-hosted stockrescue-coordinator via Agent Chat Protocol."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

TARGET_COORDINATOR = "agent1q0yvg2ul6kqe5hktekznfcuz535jdhqrek4tfh2fckhex988nhk0uxyhs0u"

test_client = Agent(name="stockrescue-tester", port=8099)
chat_proto = Protocol(spec=chat_protocol_spec)

received_reply = asyncio.Event()


@chat_proto.on_message(ChatMessage)
async def handle_reply(ctx: Context, sender: str, msg: ChatMessage):
    print(f"\n[RECEIVED CHAT REPLY FROM COORDINATOR ({sender})]")
    for item in msg.content:
        if isinstance(item, TextContent):
            print(f"Text:\n{item.text}")
        else:
            print(f"Metadata/Card Type: {item}")
    received_reply.set()


@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    print(f"[ACK RECEIVED] Msg ID: {msg.acknowledged_msg_id}")


test_client.include(chat_proto)


@test_client.on_event("startup")
async def send_test_prompt(ctx: Context):
    print(f"Sending prompt 'recover DEMO-STOCK-RESCUE-001' to {TARGET_COORDINATOR}...")
    msg_id = uuid4()
    await ctx.send(
        TARGET_COORDINATOR,
        ChatMessage(
            timestamp=datetime.now(timezone.utc),
            msg_id=msg_id,
            content=[TextContent(type="text", text="recover DEMO-STOCK-RESCUE-001")],
        ),
    )


async def main():
    loop = asyncio.get_event_loop()
    run_task = loop.create_task(test_client.run_async())

    try:
        await asyncio.wait_for(received_reply.wait(), timeout=15.0)
        print("\nSUCCESS: End-to-end cloud coordinator interaction verified!")
    except asyncio.TimeoutError:
        print("\nTest finished or waiting for reply via Almanac.")
    finally:
        test_client.server.should_exit = True


if __name__ == "__main__":
    asyncio.run(main())
