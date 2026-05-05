from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CheckResult:
    ok: bool
    message: str
