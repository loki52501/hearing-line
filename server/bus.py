"""In-memory pub/sub per call_id. Owner: lead."""


async def publish(call_id: str, event: dict) -> None:
    return None


def emitter(call_id: str):
    async def emit(event: dict) -> None:
        await publish(call_id, event)
    return emit
