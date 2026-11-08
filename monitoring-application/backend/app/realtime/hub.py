"""In-memory live window and WebSocket fan-out for this process.

The simulator publishes each tick here before the batch write, so the live window and the push
include readings that are not saved yet. On start the window is primed from saved system records,
so a restart does not show an empty screen. The hub also ends connections whose login has ended
(PRD-08-R5): one query every SWEEP_SECONDS covers every connected user.
"""

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.errors import NotAuthenticatedError, SessionLoggedOutError, SessionSupersededError, TokenExpiredError
from app.core.errors import AppError, ErrorBody
from app.models.category_setting import CategorySetting
from app.models.record import Record
from app.models.user import User
from app.realtime.schemas import (
    AnomalyItem,
    ErrorMessage,
    GeneratorState,
    HeartbeatMessage,
    LiveCategory,
    LiveReading,
    LiveSnapshot,
    LiveSummary,
    Point,
    ReadingsMessage,
    SnapshotMessage,
)
from app.records.anomaly import CategoryRange
from app.simulator.devices import DEVICES, Device

logger = logging.getLogger("app.realtime")

WINDOW_SECONDS = 300
RECENT_ANOMALY_LIMIT = 20
SWEEP_SECONDS = 2.0
SUBSCRIBER_QUEUE_SIZE = 100


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def aware(value: datetime | None) -> datetime | None:
    return None if value is None else value.replace(tzinfo=UTC)


def stale_after(interval_seconds: float) -> float:
    return 2 * interval_seconds + 10


@dataclass(frozen=True)
class Reading:
    """One generated reading. timestamp is naive UTC, like the records column."""

    category: str
    title: str
    value: float
    unit: str | None
    range: CategoryRange | None
    is_anomaly: bool
    timestamp: datetime

    def point(self) -> Point:
        return Point(value=self.value, timestamp=aware(self.timestamp), is_anomaly=self.is_anomaly)

    def live(self) -> LiveReading:
        return LiveReading(
            category=self.category,
            title=self.title,
            value=self.value,
            unit=self.unit,
            range=self.range,
            is_anomaly=self.is_anomaly,
            timestamp=aware(self.timestamp),
        )

    def anomaly(self) -> AnomalyItem:
        return AnomalyItem(
            category=self.category,
            title=self.title,
            value=self.value,
            unit=self.unit,
            range=self.range,
            timestamp=aware(self.timestamp),
        )


@dataclass(frozen=True)
class CategoryDisplay:
    range: CategoryRange | None
    unit: str | None


@dataclass(frozen=True)
class Close:
    """Queued for a subscriber to end its connection."""

    code: int
    error: AppError | None = None


@dataclass(eq=False)
class Subscriber:
    user_id: int
    session_id: str
    expires_at: datetime  # aware UTC
    queue: asyncio.Queue[dict[str, Any] | Close] = field(
        default_factory=lambda: asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
    )
    closing: bool = False

    def close(self, code: int, error: AppError | None = None) -> None:
        if self.closing:
            return
        self.closing = True
        # Make room: a close must get through even to a client that stopped reading.
        while self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(Close(code, error))

    def offer(self, message: dict[str, Any]) -> None:
        if self.closing:
            return
        if self.queue.full():
            # Backpressure: a client this far behind is dropped and may reconnect for a fresh snapshot.
            self.close(1013)
            return
        self.queue.put_nowait(message)


class RealtimeHub:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        interval_seconds: float,
        devices: Iterable[Device] = DEVICES,
        sweep_seconds: float = SWEEP_SECONDS,
    ) -> None:
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds
        self.devices = tuple(devices)
        self.sweep_seconds = sweep_seconds
        self.generator_running = False
        self.last_tick_at: datetime | None = None
        self._readings: deque[Reading] = deque()
        self._latest: dict[str, Reading] = {}
        self._anomalies: deque[Reading] = deque(maxlen=RECENT_ANOMALY_LIMIT)
        self._display: dict[str, CategoryDisplay] = {}
        self._subscribers: set[Subscriber] = set()
        self._sweeper: asyncio.Task[None] | None = None

    @property
    def stale_after_seconds(self) -> float:
        return stale_after(self.interval_seconds)

    # --- Lifecycle ---

    async def start(self) -> None:
        try:
            await self.prime()
        except SQLAlchemyError:
            logger.warning("live window could not be primed from saved records", exc_info=True)
        self._sweeper = asyncio.create_task(self._sweep_forever(), name="realtime-session-sweep")

    async def stop(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper
        for subscriber in list(self._subscribers):
            subscriber.close(1001)

    async def prime(self) -> None:
        categories = [device.category for device in self.devices]
        titles = {device.category: device.title for device in self.devices}
        since = utc_now() - timedelta(seconds=WINDOW_SECONDS)
        async with self.session_factory() as session:
            await self.refresh_display(session)
            system = (Record.created_by_user_id.is_(None), Record.category.in_(categories))
            window = await session.execute(
                select(Record.category, Record.value, Record.is_anomaly, Record.timestamp)
                .where(*system, Record.timestamp >= since)
                .order_by(Record.timestamp, Record.id)
            )
            anomalies = await session.execute(
                select(Record.category, Record.title, Record.value, Record.timestamp)
                .where(*system, Record.is_anomaly.is_(True))
                .order_by(Record.timestamp.desc(), Record.id.desc())
                .limit(RECENT_ANOMALY_LIMIT)
            )
            await session.commit()
        for row in window:
            display = self._display.get(row.category, CategoryDisplay(None, None))
            self._remember(
                Reading(row.category, titles[row.category], row.value, display.unit, display.range, row.is_anomaly, row.timestamp),
                track_anomaly=False,
            )
        for row in reversed(anomalies.all()):
            display = self._display.get(row.category, CategoryDisplay(None, None))
            self._anomalies.append(
                Reading(row.category, row.title, row.value, display.unit, display.range, True, row.timestamp)
            )

    async def refresh_display(self, session: AsyncSession) -> None:
        """Current ranges and units of the device categories. Leaves the read open for the caller."""
        rows = await session.execute(
            select(CategorySetting.category, CategorySetting.range_min, CategorySetting.range_max, CategorySetting.unit)
            .where(CategorySetting.category.in_([device.category for device in self.devices]))
        )
        display = {}
        for row in rows:
            category_range = None if row.range_min is None else CategoryRange(min=row.range_min, max=row.range_max)
            display[row.category] = CategoryDisplay(category_range, row.unit)
        self._display = display

    def display_of(self, category: str) -> CategoryDisplay:
        return self._display.get(category, CategoryDisplay(None, None))

    # --- Publishing ---

    def _remember(self, reading: Reading, *, track_anomaly: bool = True) -> None:
        self._readings.append(reading)
        latest = self._latest.get(reading.category)
        if latest is None or latest.timestamp <= reading.timestamp:
            self._latest[reading.category] = reading
        if track_anomaly and reading.is_anomaly:
            self._anomalies.append(reading)

    def _evict(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=WINDOW_SECONDS)
        while self._readings and self._readings[0].timestamp < cutoff:
            self._readings.popleft()

    def publish(self, readings: list[Reading], tick_at: datetime) -> None:
        self.last_tick_at = tick_at
        for reading in readings:
            self._remember(reading)
        now = utc_now()
        self._evict(now)
        counts = self._anomaly_counts(now)
        message = ReadingsMessage(
            server_time=aware(now),
            tick_at=aware(tick_at),
            readings=[reading.live() for reading in readings],
            summary=self._summary(now, counts),
            category_anomaly_counts_5m=counts,
        ).model_dump(mode="json")
        for subscriber in list(self._subscribers):
            subscriber.offer(message)

    # --- Views ---

    def _window(self, now: datetime) -> list[Reading]:
        cutoff = now - timedelta(seconds=WINDOW_SECONDS)
        return [reading for reading in self._readings if reading.timestamp >= cutoff]

    def _anomaly_counts(self, now: datetime) -> dict[str, int]:
        counts = {device.category: 0 for device in self.devices}
        for reading in self._window(now):
            if reading.is_anomaly and reading.category in counts:
                counts[reading.category] += 1
        return counts

    def last_reading_at(self) -> datetime | None:
        return max((reading.timestamp for reading in self._latest.values()), default=None)

    def generator_state(self) -> GeneratorState:
        return GeneratorState(running=self.generator_running, last_tick_at=aware(self.last_tick_at))

    def _summary(self, now: datetime, counts: dict[str, int]) -> LiveSummary:
        last = self.last_reading_at()
        return LiveSummary(
            anomalous_category_count=sum(
                1 for device in self.devices if (r := self._latest.get(device.category)) is not None and r.is_anomaly
            ),
            category_count=len(self.devices),
            anomaly_count_5m=sum(counts.values()),
            last_reading_at=aware(last),
            stalled=last is not None and (now - last).total_seconds() > self.stale_after_seconds,
        )

    def snapshot(self) -> LiveSnapshot:
        now = utc_now()
        window = self._window(now)
        counts = self._anomaly_counts(now)
        categories = []
        for device in self.devices:
            display = self._display.get(device.category, CategoryDisplay(None, None))
            latest = self._latest.get(device.category)
            categories.append(
                LiveCategory(
                    category=device.category,
                    title=device.title,
                    unit=display.unit,
                    range=display.range,
                    range_status="unset" if display.range is None else "set",
                    latest=latest.point() if latest else None,
                    anomaly_count_5m=counts[device.category],
                    readings=[reading.point() for reading in window if reading.category == device.category],
                )
            )
        return LiveSnapshot(
            server_time=aware(now),
            interval_seconds=self.interval_seconds,
            window_seconds=WINDOW_SECONDS,
            stale_after_seconds=self.stale_after_seconds,
            generator=self.generator_state(),
            summary=self._summary(now, counts),
            categories=categories,
            recent_anomalies=[reading.anomaly() for reading in reversed(self._anomalies)],
        )

    def snapshot_message(self) -> dict[str, Any]:
        return SnapshotMessage(**self.snapshot().model_dump()).model_dump(mode="json")

    def heartbeat_message(self) -> dict[str, Any]:
        return HeartbeatMessage(
            server_time=aware(utc_now()),
            last_reading_at=aware(self.last_reading_at()),
            generator=self.generator_state(),
        ).model_dump(mode="json")

    @staticmethod
    def error_message(error: AppError) -> dict[str, Any]:
        return ErrorMessage(error=ErrorBody(code=error.code, message=error.message)).model_dump(mode="json")

    # --- Subscribers ---

    def subscribe(self, user_id: int, session_id: str, expires_at: datetime) -> Subscriber:
        subscriber = Subscriber(user_id=user_id, session_id=session_id, expires_at=expires_at)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def sweep(self) -> None:
        """Ends connections whose login was replaced, logged out, removed or expired."""
        subscribers = [subscriber for subscriber in self._subscribers if not subscriber.closing]
        if not subscribers:
            return
        async with self.session_factory() as session:
            rows = await session.execute(
                select(User.id, User.current_session_id).where(User.id.in_({s.user_id for s in subscribers}))
            )
            current = {row.id: row.current_session_id for row in rows}
            await session.commit()
        now = datetime.now(UTC)
        for subscriber in subscribers:
            error: AppError | None = None
            if subscriber.user_id not in current:
                error = NotAuthenticatedError()
            elif current[subscriber.user_id] is None:
                error = SessionLoggedOutError()
            elif current[subscriber.user_id] != subscriber.session_id:
                error = SessionSupersededError()
            elif subscriber.expires_at <= now:
                error = TokenExpiredError()
            if error is not None:
                subscriber.close(4401, error)

    async def _sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(self.sweep_seconds)
            try:
                await self.sweep()
            except SQLAlchemyError:
                # Connections stay open while the database is away; the next sweep tries again.
                logger.warning("realtime session check failed", exc_info=True)
