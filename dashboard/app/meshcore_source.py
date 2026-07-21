"""MeshCore node (Board 2) over WiFi/TCP companion, via the `meshcore` lib.

Unlike the Meshtastic library (blocking, runs in a thread), meshcore is
asyncio-native, so this source runs as a task on the FastAPI event loop.
MeshCore allows multiple simultaneous companion connections, so the phone
app and this dashboard can both talk to the node at once.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .state import DashboardState

log = logging.getLogger("meshcore_source")

_RECONNECT_SECS = 15
_REFRESH_SECS = 30
# Contention-aware auto-yield: Board 2's WiFi firmware is effectively
# single-client (last connection wins), so the phone and the dashboard fight
# over one slot. A healthy session the phone isn't touching lasts far longer
# than _CONTEND_SECS; a phone actively reclaiming kills our session in a few
# seconds. After _CONTEND_THRESHOLD consecutive short sessions we assume the
# phone has it and back off for _YIELD_SECS (probing once per interval) instead
# of kicking the phone every reconnect. We reclaim automatically once a probe
# holds steady (phone disconnected).
_CONTEND_SECS = 25
_CONTEND_THRESHOLD = 2
_YIELD_SECS = 120


class MeshCoreSource:
    def __init__(self, state: DashboardState, host: str, port: int = 5000):
        self.state = state
        self.host = host
        self.port = port
        self.mc = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._channels: dict[int, str] = {}  # idx -> name, for tagging messages
        # Runtime "release the single TCP slot to the phone" toggle.
        self._paused = False
        self._wake = asyncio.Event()
        # Consecutive short-lived sessions — the signature of the phone holding
        # the slot. Drives the polite auto-yield backoff.
        self._short_sessions = 0

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
        self._short_sessions = 0  # user explicitly wants it back — probe now
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
        from meshcore import EventType
        contact = mc.get_contact_by_name(to)
        if contact is None:
            # Not on the node's live list — re-add it from the durable contact
            # log so the firmware has a routing record, then send. (MeshCore
            # returns ERR_CODE_NOT_FOUND if you try to message a bare pubkey it
            # doesn't hold as a contact.)
            contact = await self._readd_from_log(to)
        result = await mc.commands.send_msg(contact, text)
        if getattr(result, "type", None) == EventType.ERROR:
            raise RuntimeError(f"send failed: {result.payload}")
        self.state.add_meshcore_message({
            "network": "meshcore", "direction": "tx",
            "from": "me", "to": to, "text": text,
        })

    async def send_channel(self, idx: int, text: str) -> None:
        mc = self.mc
        if mc is None:
            raise RuntimeError("MeshCore node is not connected")
        from meshcore import EventType
        result = await mc.commands.send_chan_msg(int(idx), text)
        if getattr(result, "type", None) == EventType.ERROR:
            raise RuntimeError(f"send failed: {result.payload}")
        self.state.add_meshcore_channel_message({
            "network": "meshcore", "direction": "tx",
            "channel_idx": int(idx), "channel": self._channel_name(idx),
            "from": "me", "text": text,
        })

    def _channel_name(self, idx) -> str:
        if idx is None:
            return "?"
        return self._channels.get(int(idx), f"ch{idx}")

    async def _readd_from_log(self, to: str):
        """Rebuild a node contact record from the DB log and add it back, so a
        contact that aged off the node (or was cleared by a reboot) becomes
        messageable again. Adds it as a flood contact (no cached path)."""
        row = None
        if self.state.persistence is not None:
            try:
                row = self.state.persistence.find_contact_by_name(to)
            except Exception:
                row = None
        if row is None:
            raise RuntimeError(f"no MeshCore contact named {to!r}")
        ctype = row.get("type")
        if ctype == 2:
            raise RuntimeError(f"{to!r} is a repeater, not a messageable contact")
        if ctype == 3:
            raise RuntimeError(
                f"{to!r} is a room server — join/post to the room, "
                "it's not a direct-message contact")
        data = row.get("data") or {}
        key = row.get("key") or data.get("public_key")
        if not key:
            raise RuntimeError(f"no public key on record for {to!r}")
        lat = row.get("lat") if row.get("lat") is not None else data.get("adv_lat")
        lon = row.get("lon") if row.get("lon") is not None else data.get("adv_lon")
        contact = {
            "public_key": key,
            "type": int(ctype or 1),
            "flags": 0,
            "out_path": "",
            "out_path_len": -1,            # -1 -> flood (no cached path)
            "out_path_hash_mode": 0,
            "adv_name": row.get("name") or to,
            "last_advert": int(data.get("last_advert") or 0),
            "adv_lat": float(lat or 0),
            "adv_lon": float(lon or 0),
        }
        from meshcore import EventType
        r = await self.mc.commands.add_contact(contact)
        if getattr(r, "type", None) == EventType.ERROR:
            raise RuntimeError(f"couldn't re-add {to!r}: {r.payload}")
        await self._refresh_contacts()
        live = self.mc.get_contact_by_name(to)
        return live if live is not None else contact

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
                self._short_sessions = 0
                self._wake.clear()
                await self._wake.wait()  # until resume() or stop()
                continue
            connected_at: float | None = None
            try:
                self.state.set_source_status(
                    "meshcore", False, f"connecting to {self.host}:{self.port}")
                self.mc = await MeshCore.create_tcp(self.host, self.port, auto_reconnect=False)

                self.mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_message)
                chan = getattr(EventType, "CHANNEL_MSG_RECV", None)
                if chan is not None:
                    self.mc.subscribe(chan, self._on_channel_message)

                await self.mc.start_auto_message_fetching()
                await self._refresh_self()
                await self._refresh_contacts()
                await self._refresh_channels()
                connected_at = time.monotonic()
                self.state.set_source_status(
                    "meshcore", True, f"logging · {self.host}:{self.port}")

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
            if self._paused or self._stop.is_set():
                continue
            # Contention accounting: a session that held past _CONTEND_SECS means
            # we had the slot to ourselves; a short one means we're being kicked.
            session = (time.monotonic() - connected_at) if connected_at else 0.0
            if session >= _CONTEND_SECS:
                self._short_sessions = 0
            else:
                self._short_sessions += 1
            if self._short_sessions >= _CONTEND_THRESHOLD:
                self.state.set_source_status(
                    "meshcore", False,
                    "yielding to phone — will reclaim automatically when it disconnects")
                await self._interruptible_sleep(_YIELD_SECS)
            else:
                await self._interruptible_sleep(_RECONNECT_SECS)

    # -- event handlers -----------------------------------------------------

    async def _on_message(self, event) -> None:
        p = getattr(event, "payload", None) or {}
        self.state.add_meshcore_message({
            "network": "meshcore",
            "direction": "rx",
            "from": p.get("pubkey_prefix") or p.get("from") or "?",
            "text": p.get("text", ""),
        })

    async def _on_channel_message(self, event) -> None:
        p = getattr(event, "payload", None) or {}
        idx = p.get("channel_idx")
        text = p.get("text", "")
        # Channel packets carry no sender pubkey; the sender's node prepends its
        # own name as "Name: message". Split it out for display.
        sender = None
        if ": " in text:
            head, rest = text.split(": ", 1)
            if head and len(head) <= 32:
                sender, text = head, rest
        # path_len: 0..62 = hops, 255 = direct/flood (no cached path).
        plen = p.get("path_len")
        hops = plen if isinstance(plen, int) and 0 <= plen < 63 else None
        self.state.add_meshcore_channel_message({
            "network": "meshcore",
            "direction": "rx",
            "channel_idx": idx,
            "channel": self._channel_name(idx),
            "from": sender or "(unnamed)",
            "text": text,
            "hops": hops,
            "sender_time": p.get("sender_timestamp"),
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
                "adv_lat": c.get("adv_lat"),
                "adv_lon": c.get("adv_lon"),
                # cached routing path length: 0 = heard direct, N = N hops,
                # <0 = flood (no path yet). Surfaced as "hops" on the map.
                "path_len": c.get("out_path_len"),
            })
        self.state.set_meshcore_contacts(out)

    async def _refresh_channels(self, max_slots: int = 8) -> None:
        """Read the node's channel slots. Channels rarely change, so this runs
        once per connect. Only names are surfaced — secrets stay on the node."""
        from meshcore import EventType
        mc = self.mc
        if mc is None:
            return
        chans: dict[int, str] = {}
        out: list[dict[str, Any]] = []
        for i in range(max_slots):
            try:
                r = await mc.commands.get_channel(i)
            except Exception:
                break
            if getattr(r, "type", None) == EventType.ERROR:
                continue
            p = r.payload or {}
            name = (p.get("channel_name") or "").strip()
            if not name:
                continue  # empty slot
            chans[i] = name
            out.append({"idx": i, "name": name})
        self._channels = chans
        self.state.set_meshcore_channels(out)
