from __future__ import annotations

import asyncio
import csv
import io
import logging

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field

log = logging.getLogger("api")
router = APIRouter()


class SendRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200)


class MeshCoreSendRequest(BaseModel):
    to: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=200)


class AdvertRequest(BaseModel):
    flood: bool = True


class RadioRequest(BaseModel):
    freq: float = Field(gt=400, lt=1000)
    bw: float = Field(gt=0, lt=1000)
    sf: int = Field(ge=5, le=12)
    cr: int = Field(ge=5, le=8)


@router.get("/api/status")
def status(request: Request):
    snap = request.app.state.dashboard.snapshot()
    return {"ok": True, "sources": snap["sources"], "reticulum": snap["reticulum"]}


@router.get("/api/nodes")
def nodes(request: Request):
    return request.app.state.dashboard.snapshot()["nodes"]


@router.get("/api/messages")
def messages(request: Request):
    return request.app.state.dashboard.snapshot()["messages"]


@router.post("/api/send")
async def send(req: SendRequest, request: Request):
    source = request.app.state.meshtastic
    try:
        # sendText does blocking socket I/O; keep it off the event loop.
        await asyncio.to_thread(source.send_text, req.text)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.exception("send failed")
        raise HTTPException(status_code=502, detail=f"send failed: {exc}")
    return {"ok": True}


@router.post("/api/meshcore/send")
async def meshcore_send(req: MeshCoreSendRequest, request: Request):
    source = request.app.state.meshcore
    try:
        await source.send_text(req.to, req.text)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.exception("meshcore send failed")
        raise HTTPException(status_code=502, detail=f"send failed: {exc}")
    return {"ok": True}


@router.post("/api/meshcore/pause")
async def meshcore_pause(request: Request):
    request.app.state.meshcore.pause()
    return {"ok": True, "paused": True}


@router.post("/api/meshcore/resume")
async def meshcore_resume(request: Request):
    request.app.state.meshcore.resume()
    return {"ok": True, "paused": False}


@router.post("/api/meshcore/advert")
async def meshcore_advert(req: AdvertRequest, request: Request):
    try:
        await request.app.state.meshcore.send_advert(req.flood)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"advert failed: {exc}")
    return {"ok": True}


@router.post("/api/meshcore/radio")
async def meshcore_radio(req: RadioRequest, request: Request):
    try:
        await request.app.state.meshcore.set_radio(req.freq, req.bw, req.sf, req.cr)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"set radio failed: {exc}")
    return {"ok": True}


@router.post("/api/meshtastic/pause")
async def meshtastic_pause(request: Request):
    request.app.state.meshtastic.pause()
    return {"ok": True, "paused": True}


@router.post("/api/meshtastic/resume")
async def meshtastic_resume(request: Request):
    request.app.state.meshtastic.resume()
    return {"ok": True, "paused": False}


@router.get("/api/meshcore/contacts")
def meshcore_contacts(request: Request):
    """The durable contact log — every MeshCore contact ever seen, even ones
    later pruned off the node."""
    p = request.app.state.dashboard.persistence
    rows = p.load_contacts() if p is not None else []
    return {"count": len(rows), "contacts": rows}


@router.get("/api/meshcore/contacts.csv")
def meshcore_contacts_csv(request: Request):
    p = request.app.state.dashboard.persistence
    rows = p.load_contacts() if p is not None else []
    cols = ["key", "name", "type", "first_seen", "last_seen", "last_advert", "lat", "lon"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c) for c in cols])
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=meshcore_contacts.csv"},
    )


@router.websocket("/ws")
async def ws(websocket: WebSocket):
    state = websocket.app.state.dashboard
    await websocket.accept()
    try:
        last_version = -1
        while True:
            snap = state.snapshot()
            if snap["version"] != last_version:
                last_version = snap["version"]
                await websocket.send_json(snap)
            # Wake on change, but also heartbeat so clients can detect
            # a dead backend even when nothing on the mesh is talking.
            try:
                await asyncio.wait_for(state.wait_for_change(), timeout=30)
            except asyncio.TimeoutError:
                last_version = -1  # force a resend as heartbeat
    except WebSocketDisconnect:
        pass
