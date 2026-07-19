"""MeshCore node (Board 2) over WiFi/TCP companion, via the `meshcore` lib.

Unlike the Meshtastic library (blocking, runs in a thread), meshcore is
asyncio-native, so this source runs as a task on the FastAPI event loop.
MeshCore allows multiple simultaneous companion connections, so the phone
app and this dashboard can both talk to the node at once.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .state import DashboardState

log = logging.getLogger("meshcore_source")

_RECONNECT_SECS = 15
_REFRESH_SECS = 30


class MeshCoreSource:
    def __init__(self, state: DashboardState, host: str, port: int = 5000):
        self.state = state
        self.host = host
        self.port = port
        self.mc = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # Runtime "release the single TCP slot to the phone" toggle.
        self._paused = False
        self._wake = asyncio.Event()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if not self.host:
            self.state.set_source_status(
                "meshcore", False,
                "disabled — set MESHCORE_HOST once Board 2 has WiFi",
            )
            return
        self._task = asyncio.create_task(self._run())

    def pause(self) -> None:
        """Release Board 2's single TCP companion slot (e.g. to the phone)."""
        self._paused = True
        self._wake.set()

    def resume(self) -> None:
        """Reclaim the connection for the dashboard."""
        self._paused = False
        self._wake.set()

    @property
    def paused(self) -> bool:
        return self._paused

    async def _interruptible_sleep(self, secs: float) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=secs)
        except asyncio.TimeoutError:
            pass

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        mc = self.mc
        if mc is not None:
            try:
                await mc.disconnect()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def send_text(self, to: str, text: str) -> None:
        mc = self.mc
        if mc is None:
            raise RuntimeError("MeshCore node is not connected")
        contact = mc.get_contact_by_name(to)
        if contact is None:
            raise RuntimeError(f"no MeshCore contact named {to!r}")
        from meshcore import EventType
        result = await mc.commands.send_msg(contact, text)
        if getattr(result, "type", None) == EventType.ERROR:
            raise RuntimeError(f"send failed: {result.payload}")
        self.state.add_meshcore_message({
            "network": "meshcore", "direction": "tx",
            "from": "me", "to": to, "text": text,
        })

    async def send_advert(self, flood: bool = True) -> None:
        mc = self.mc
        if mc is None:
            raise RuntimeError("MeshCore node not connected (released to the phone?)")
        await mc.commands.send_advert(flood)

    async def set_radio(self, freq: float, bw: float, sf: int, cr: int) -> None:
        mc = self.mc
        if mc is None:
            raise RuntimeError("MeshCore node not connected (released to the phone?)")
        from meshcore import EventType
        r = await mc.commands.set_radio(float(freq), float(bw), int(sf), int(cr))
        if getattr(r, "type", None) == EventType.ERROR:
            raise RuntimeError(f"set_radio failed: {r.payload}")
        await self._refresh_self()

    # -- connection loop ----------------------------------------------------

    async def _run(self) -> None:
        # auto_reconnect=False so a pause (or the phone taking the slot) lets
        # this loop cleanly release rather than the lib reconnecting under us.
        from meshcore import MeshCore, EventType
        while not self._stop.is_set():
            if self._paused:
                self.state.set_source_status(
                    "meshcore", False, "paused — Board 2 released to the phone/app")
                self._wake.clear()
                await self._wake.wait()  # until resume() or stop()
                continue
            try:
                self.state.set_source_status(
                    "meshcore", False, f"connecting to {self.host}:{self.port}")
                self.mc = await MeshCore.create_tcp(self.host, self.port, auto_reconnect=False)

                self.mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_message)
                chan = getattr(EventType, "CHANNEL_MSG_RECV", None)
                if chan is not None:
                    self.mc.subscribe(chan, self._on_message)

                await self.mc.start_auto_message_fetching()
                await self._refresh_self()
                await self._refresh_contacts()
                self.state.set_source_status("meshcore", True, f"{self.host}:{self.port}")

                while not self._stop.is_set() and not self._paused and self.mc.is_connected:
                    await self._interruptible_sleep(_REFRESH_SECS)
                    if self._stop.is_set() or self._paused:
                        break
                    await self._refresh_self()
                    await self._refresh_contacts()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("meshcore connection error: %s", exc)
                self.state.set_source_status("meshcore", False, str(exc))
            finally:
                mc, self.mc = self.mc, None
                if mc is not None:
                    try:
                        await mc.disconnect()
                    except Exception:
                        pass
            if not self._paused and not self._stop.is_set():
                await self._interruptible_sleep(_RECONNECT_SECS)

    # -- event handlers -----------------------------------------------------

    async def _on_message(self, event) -> None:
        p = getattr(event, "payload", None) or {}
        self.state.add_meshcore_message({
            "network": "meshcore",
            "direction": "rx",
            "from": p.get("pubkey_prefix") or p.get("from") or p.get("channel") or "?",
            "text": p.get("text", ""),
        })

    # -- refreshers ---------------------------------------------------------

    async def _refresh_self(self) -> None:
        from meshcore import EventType
        mc = self.mc
        if mc is None:
            return
        try:
            r = await mc.commands.send_appstart()
            if getattr(r, "type", None) != EventType.ERROR:
                info = r.payload or {}
                self.state.set_meshcore_self({
                    "name": info.get("name") or info.get("adv_name"),
                    "public_key": info.get("public_key"),
                    "radio_freq": info.get("radio_freq"),
                    "radio_bw": info.get("radio_bw"),
                    "radio_sf": info.get("radio_sf"),
                    "radio_cr": info.get("radio_cr"),
                    "tx_power": info.get("tx_power"),
                })
        except Exception as exc:
            log.debug("appstart failed: %s", exc)
        try:
            b = await mc.commands.get_bat()
            if getattr(b, "type", None) != EventType.ERROR:
                bat = b.payload or {}
                level = bat.get("level")
                fields: dict[str, Any] = {}
                # MeshCore reports the battery as millivolts, not a percent.
                if isinstance(level, (int, float)) and level > 100:
                    fields["voltage"] = round(level / 1000, 2)
                    fields["battery"] = max(0, min(100, round((level - 3300) / 9)))
                elif level is not None:
                    fields["battery"] = level
                if bat.get("voltage") is not None:
                    fields["voltage"] = bat.get("voltage")
                if fields:
                    self.state.set_meshcore_self(fields)
        except Exception as exc:
            log.debug("get_bat failed: %s", exc)

    async def _refresh_contacts(self) -> None:
        from meshcore import EventType
        mc = self.mc
        if mc is None:
            return
        try:
            r = await mc.commands.get_contacts()
        except Exception as exc:
            log.debug("get_contacts failed: %s", exc)
            return
        if getattr(r, "type", None) == EventType.ERROR:
            return
        contacts = r.payload or {}
        out: list[dict[str, Any]] = []
        for key, c in contacts.items():
            out.append({
                "key": key,
                "name": c.get("adv_name") or c.get("name") or str(key)[:8],
                "public_key": c.get("public_key"),
                "last_advert": c.get("last_advert") or c.get("adv_timestamp"),
                "type": c.get("type"),
            })
        self.state.set_meshcore_contacts(out)
