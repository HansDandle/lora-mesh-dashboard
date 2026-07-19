"""Reticulum panel data source.

Board 2 is not flashed yet and RTNode-HeltecV4 exposes no documented
programmatic status API (OLED + serial console only), so the real data
shape is unknown. This module defines the interface the panel renders
from, plus an honest "awaiting firmware" stub. Once the board is flashed
and its actual surface is known (likely serial-console scraping or an
RNS-level probe), implement a new ReticulumSource and swap it in main.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .state import DashboardState


class ReticulumSource(ABC):
    """get_status() returns:
    {
      "state": "unknown" | "offline" | "online",
      "detail": str,            # human-readable one-liner
      "uptime": int | None,     # seconds
      "interfaces": {"lora": ..., "wifi": ..., "wan": ..., "lan": ...},
    }
    """

    def __init__(self, state: DashboardState):
        self.state = state

    @abstractmethod
    def get_status(self) -> dict[str, Any]: ...

    def start(self) -> None:
        self.state.set_reticulum(self.get_status())

    def stop(self) -> None:
        pass


class NotFlashedYet(ReticulumSource):
    """Placeholder until Board 2 is flashed with RTNode-HeltecV4."""

    def get_status(self) -> dict[str, Any]:
        return {
            "state": "unknown",
            "detail": "Board 2 not yet flashed — see docs/reticulum-board2-flash.md",
            "uptime": None,
            "interfaces": {},
        }
