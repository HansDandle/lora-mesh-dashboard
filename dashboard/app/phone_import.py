"""Import a MeshCore phone-app database export into the dashboard.

The MeshCore Android/iOS app can export its SQLite database. Because the node
hands each message to exactly one companion (delivery pops the node's queue),
history that only reached the phone can't be recovered any other way — so this
importer folds the phone's own record (DMs, channel messages, contacts) into
the dashboard's durable log. It's idempotent: re-importing the same or an
overlapping export won't duplicate rows (see Persistence.import_message).
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any


def _hx(v: Any) -> str | None:
    """Pubkeys are stored as BLOBs in the phone DB; normalise to lowercase hex."""
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).hex()
    return str(v).lower()


def _digest(*parts: Any) -> str:
    h = hashlib.sha1("\x1f".join("" if p is None else str(p) for p in parts).encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def _hops(path_len: Any) -> int | None:
    # Phone stores the raw path byte: high bits are the hash mode, low 6 bits
    # the hop count; 255/-1 means flood (no cached path).
    if not isinstance(path_len, int) or path_len < 0 or path_len == 255:
        return None
    h = path_len & 0x3F
    return h if 0 <= h < 63 else None


def _split_sender(text: str) -> tuple[str | None, str]:
    """Channel packets carry no sender key; the sender's node prepends
    'Name: message'. Split it out for display (same as the live handler)."""
    if ": " in text:
        head, rest = text.split(": ", 1)
        if head and len(head) <= 32:
            return head, rest
    return None, text


def import_phone_db(path: str, persistence) -> dict[str, int]:
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    counts = {"channel_messages": 0, "contact_messages": 0, "contacts": 0, "skipped": 0}
    try:
        tables = {r[0] for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

        # secret(hex) -> (idx, name)
        chan_by_secret: dict[str, tuple[int, str]] = {}
        if "channels" in tables:
            for r in src.execute("SELECT channel_idx, name, hex(secret) AS s FROM channels"):
                if r["s"]:
                    chan_by_secret[r["s"].upper()] = (r["channel_idx"], r["name"] or f"ch{r['channel_idx']}")

        # pubkey(hex) -> display name, for DM ends
        name_by_key: dict[str, str] = {}
        for t in ("contacts", "discovered_contacts"):
            if t in tables:
                for r in src.execute(f"SELECT public_key, adv_name, custom_name FROM {t}"
                                     if t == "contacts"
                                     else f"SELECT public_key, adv_name FROM {t}"):
                    nm = (r["custom_name"] if "custom_name" in r.keys() and r["custom_name"] else None) \
                        or r["adv_name"]
                    pk = _hx(r["public_key"])
                    if pk and nm:
                        name_by_key.setdefault(pk, nm.strip())

        # our own pubkey: the sender of any non-received message
        self_key = None
        if "contact_messages" in tables:
            row = src.execute(
                "SELECT \"from\" FROM contact_messages WHERE status != 'received' "
                "AND \"from\" IS NOT NULL LIMIT 1").fetchone()
            if row:
                self_key = _hx(row[0])

        # ---- channel messages ----
        if "channel_messages" in tables:
            for r in src.execute(
                    "SELECT hex(channel_secret) AS cs, path_len, sender_timestamp, "
                    "text, timestamp, snr FROM channel_messages"):
                idx, cname = chan_by_secret.get((r["cs"] or "").upper(), (None, "?"))
                sender, text = _split_sender(r["text"] or "")
                when = (r["timestamp"] or 0) / 1000 or r["sender_timestamp"]
                msg = {
                    "network": "meshcore", "direction": "rx",
                    "channel_idx": idx, "channel": cname,
                    "from": sender or "(unnamed)", "text": text,
                    "hops": _hops(r["path_len"]), "snr": r["snr"],
                    "sender_time": r["sender_timestamp"], "time": when,
                    "imported": True,
                }
                key = "pc:" + _digest(r["cs"], r["sender_timestamp"], r["text"])
                if persistence.import_message("meshcore_channel", msg, key):
                    counts["channel_messages"] += 1
                else:
                    counts["skipped"] += 1

        # ---- direct messages ----
        if "contact_messages" in tables:
            for r in src.execute(
                    "SELECT status, \"to\" AS dst, \"from\" AS src, text, "
                    "sender_timestamp, timestamp, snr, path_len FROM contact_messages"):
                src_k, dst_k = _hx(r["src"]), _hx(r["dst"])
                outgoing = self_key is not None and src_k == self_key
                peer_key = (dst_k if outgoing else src_k) or ""
                peer = name_by_key.get(peer_key, peer_key[:10] or "?")
                when = (r["timestamp"] or 0) / 1000 or r["sender_timestamp"]
                msg = {
                    "network": "meshcore",
                    "direction": "tx" if outgoing else "rx",
                    "from": "me" if outgoing else peer,
                    "to": peer if outgoing else None,
                    "text": r["text"] or "", "hops": _hops(r["path_len"]),
                    "snr": r["snr"], "sender_time": r["sender_timestamp"],
                    "time": when, "imported": True,
                }
                key = "pd:" + _digest(src_k, dst_k, r["sender_timestamp"], r["text"])
                if persistence.import_message("meshcore", msg, key):
                    counts["contact_messages"] += 1
                else:
                    counts["skipped"] += 1

        # ---- contacts (durable log + map) ----
        contacts_out: list[dict[str, Any]] = []
        for t in ("contacts", "discovered_contacts"):
            if t not in tables:
                continue
            for r in src.execute(
                    f"SELECT public_key, type, adv_name, out_path_len, adv_lat, adv_lon, "
                    f"last_advert FROM {t}"):
                pk = _hx(r["public_key"])
                if not pk:
                    continue
                lat = r["adv_lat"] / 1e6 if r["adv_lat"] else None
                lon = r["adv_lon"] / 1e6 if r["adv_lon"] else None
                contacts_out.append({
                    "key": pk, "public_key": pk,
                    "name": (r["adv_name"] or "").strip() or pk[:8],
                    "type": r["type"], "last_advert": r["last_advert"],
                    "adv_lat": lat, "adv_lon": lon,
                    "path_len": r["out_path_len"],
                })
        if contacts_out:
            persistence.upsert_contacts(contacts_out)
            counts["contacts"] = len(contacts_out)
    finally:
        src.close()
    return counts
