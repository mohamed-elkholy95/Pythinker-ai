"""Channel ingress only carries a seed dict — the loop normalizes it later."""

from pythinker.bus.events import OutboundMessage
from pythinker.bus.queue import MessageBus
from pythinker.channels.base import BaseChannel


class _Stub(BaseChannel):
    name = "stub"
    display_name = "Stub"

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, msg: OutboundMessage) -> None: ...


async def test_handle_message_carries_context_seed():
    bus = MessageBus()
    ch = _Stub(config={"allow_from": ["*"]}, bus=bus)
    await ch._handle_message(sender_id="u", chat_id="c", content="hi")
    msg = await bus.consume_inbound()
    seed = msg.context_seed
    assert seed is not None
    assert seed == {"channel": "stub", "sender_id": "u", "chat_id": "c"}
    # No RequestContext yet — that's the loop's job.
    assert msg.context is None


async def test_handle_message_seed_independent_of_session_override():
    bus = MessageBus()
    ch = _Stub(config={"allow_from": ["*"]}, bus=bus)
    await ch._handle_message(
        sender_id="u", chat_id="c", content="hi", session_key="thread:42",
    )
    msg = await bus.consume_inbound()
    # The session override lives on session_key_override; the seed doesn't carry it.
    assert msg.session_key_override == "thread:42"
    assert msg.context_seed == {"channel": "stub", "sender_id": "u", "chat_id": "c"}
