"""Starts and stops the live hub and the simulator with the application (FastAPI lifespan)."""

from dataclasses import dataclass

from fastapi import FastAPI

from app.core.config import get_settings
from app.db.session import get_engine, get_session_factory
from app.realtime.hub import RealtimeHub
from app.simulator.service import FLUSH_SECONDS, Simulator


@dataclass
class RealtimeRuntime:
    hub: RealtimeHub
    simulator: Simulator

    async def stop(self) -> None:
        await self.simulator.stop()
        await self.hub.stop()


async def start_realtime(app: FastAPI) -> RealtimeRuntime:
    interval = get_settings().simulator_interval_seconds
    hub = RealtimeHub(get_session_factory(), interval_seconds=interval)
    await hub.start()
    # A batch at least every 5 s, or every tick when ticks are faster (tests).
    simulator = Simulator(
        hub, get_engine(), get_session_factory(), interval_seconds=interval, flush_seconds=min(FLUSH_SECONDS, interval)
    )
    await simulator.start()
    runtime = RealtimeRuntime(hub, simulator)
    app.state.realtime_hub = hub
    app.state.realtime_runtime = runtime
    return runtime
