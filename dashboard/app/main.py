from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import load_config
from .meshcore_source import MeshCoreSource
from .meshtastic_source import MeshtasticSource
from .mqtt_source import MqttSource
from .state import DashboardState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    state = DashboardState(message_log_size=cfg.message_log_size)
    state.attach_loop(asyncio.get_running_loop())

    persistence = None
    if cfg.db_path:
        try:
            from .persistence import Persistence
            persistence = Persistence(cfg.db_path)
            state.load_from(persistence)
            state.persistence = persistence
        except Exception as exc:
            logging.getLogger("main").warning("persistence disabled: %s", exc)

    meshtastic = MeshtasticSource(state, cfg.meshtastic_host, cfg.meshtastic_port)
    mqtt = MqttSource(state, cfg.mqtt_host, cfg.mqtt_port,
                      topic=cfg.mqtt_topic,
                      username=cfg.mqtt_username, password=cfg.mqtt_password)
    meshcore = MeshCoreSource(state, cfg.meshcore_host, cfg.meshcore_port)

    app.state.dashboard = state
    app.state.meshtastic = meshtastic
    app.state.meshcore = meshcore

    async def _sampler():
        await asyncio.sleep(5)
        while True:
            state.record_history()
            await asyncio.sleep(60)

    meshtastic.start()
    mqtt.start()
    meshcore.start()
    sampler = asyncio.create_task(_sampler())
    try:
        yield
    finally:
        sampler.cancel()
        meshtastic.stop()
        mqtt.stop()
        await meshcore.stop()
        if persistence is not None:
            persistence.close()


app = FastAPI(title="LoRa Mesh Dashboard", lifespan=lifespan)
app.include_router(router)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
