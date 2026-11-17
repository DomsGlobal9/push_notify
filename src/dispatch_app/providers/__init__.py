"""Delivery providers. Each turns (device, message) into one HTTP/2 request and
classifies the answer into one of four outcomes the dispatcher acts on:

  sent           counted, nothing else
  invalid_token  the install is gone: the device is deactivated
  retry          transient (429, 5xx, network): the device goes into a retry batch
  failed         permanent for this message (bad payload, bad credential): logged
"""

from dataclasses import dataclass
from typing import Literal

Outcome = Literal["sent", "invalid_token", "retry", "failed"]


@dataclass
class SendResult:
    outcome: Outcome
    status_code: int | None = None
    reason: str = ""
    retry_after: float | None = None


@dataclass(frozen=True)
class Target:
    device_id: int
    token: str
    platform: str
    bundle_id: str
    apns_environment: str
    android_channel_id: str


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
