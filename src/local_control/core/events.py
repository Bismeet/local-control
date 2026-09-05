"""Event models and in-process EventBus for local-control."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

EventHandler = Callable[["Event"], Awaitable[None] | None]


class Event(BaseModel):
    """Event envelope for all lifecycle, observation, and execution messages."""

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    step_index: int | None = None
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class EventBus:
    """In-process asynchronous event bus with subscriber isolation."""

    def __init__(self) -> None:
        self._subscribers: dict[str | None, list[EventHandler]] = {None: []}

    def subscribe(self, handler: EventHandler, event_type: str | None = None) -> None:
        """Register an event handler for a specific event type, or all events if None."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        if handler not in self._subscribers[event_type]:
            self._subscribers[event_type].append(handler)

    def unsubscribe(self, handler: EventHandler, event_type: str | None = None) -> None:
        """Unregister an event handler."""
        if event_type in self._subscribers and handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    async def publish(self, event: Event) -> None:
        """Publish an event to all matching subscribers, isolating subscriber errors."""
        handlers: list[EventHandler] = []
        handlers.extend(self._subscribers.get(None, []))
        if event.type in self._subscribers:
            handlers.extend(self._subscribers[event.type])

        for handler in handlers:
            try:
                res = handler(event)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(
                    "event_bus.handler_error",
                    event_id=event.event_id,
                    event_type=event.type,
                    error=str(e),
                )

    def publish_sync(self, event: Event) -> None:
        """Synchronously dispatch an event to subscribers supporting sync execution."""
        handlers: list[EventHandler] = []
        handlers.extend(self._subscribers.get(None, []))
        if event.type in self._subscribers:
            handlers.extend(self._subscribers[event.type])

        for handler in handlers:
            try:
                res = handler(event)
                if asyncio.iscoroutine(res):
                    # If called within an active loop, schedule it; else run
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        asyncio.run(res)
            except Exception as e:
                logger.error(
                    "event_bus.handler_error",
                    event_id=event.event_id,
                    event_type=event.type,
                    error=str(e),
                )
