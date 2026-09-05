"""Unit tests for EventBus, ordering, and subscriber isolation."""

import pytest

from local_control.core.events import Event, EventBus


@pytest.mark.unit
@pytest.mark.asyncio
async def test_event_bus_delivery_and_ordering() -> None:
    """Verify that events are delivered in order to subscribers."""
    bus = EventBus()
    received: list[str] = []

    async def handler(event: Event) -> None:
        received.append(event.type)

    bus.subscribe(handler)

    e1 = Event(run_id="run-1", type="step.started", payload={"step": 1})
    e2 = Event(run_id="run-1", type="action.executed", payload={"step": 1})
    e3 = Event(run_id="run-1", type="step.finished", payload={"step": 1})

    await bus.publish(e1)
    await bus.publish(e2)
    await bus.publish(e3)

    assert received == ["step.started", "action.executed", "step.finished"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_event_bus_subscriber_isolation() -> None:
    """Verify an error in one subscriber does not prevent others from receiving events."""
    bus = EventBus()
    successful_calls: list[str] = []

    def faulty_handler(event: Event) -> None:
        raise RuntimeError("Subscriber explosion!")

    def healthy_handler(event: Event) -> None:
        successful_calls.append(event.event_id)

    bus.subscribe(faulty_handler)
    bus.subscribe(healthy_handler)

    test_event = Event(run_id="run-1", type="test.event", payload={})
    await bus.publish(test_event)

    assert test_event.event_id in successful_calls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_event_bus_type_filtering() -> None:
    """Verify type filtering only dispatches matching events to specific subscribers."""
    bus = EventBus()
    all_events: list[str] = []
    filtered_events: list[str] = []

    bus.subscribe(lambda e: all_events.append(e.type), event_type=None)
    bus.subscribe(lambda e: filtered_events.append(e.type), event_type="action.confirm")

    await bus.publish(Event(run_id="run-1", type="action.safe"))
    await bus.publish(Event(run_id="run-1", type="action.confirm"))
    await bus.publish(Event(run_id="run-1", type="action.blocked"))

    assert len(all_events) == 3
    assert filtered_events == ["action.confirm"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_event_bus_unsubscribe() -> None:
    """Verify unsubscribed handlers no longer receive events."""
    bus = EventBus()
    received: list[str] = []

    def handler(event: Event) -> None:
        received.append(event.type)

    bus.subscribe(handler, event_type="test.event")
    await bus.publish(Event(run_id="run-1", type="test.event"))
    assert len(received) == 1

    bus.unsubscribe(handler, event_type="test.event")
    await bus.publish(Event(run_id="run-1", type="test.event"))
    assert len(received) == 1
