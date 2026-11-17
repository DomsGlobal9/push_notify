"""One long-lived sender per credential, reused across batches so HTTP/2
connections and OAuth/JWT tokens survive between jobs. A credential edited in
the admin bumps its revision, which retires the cached sender."""

import threading

from src.tenants_app.models import Credential

from .apns import ApnsSender
from .fcm import FcmSender

_lock = threading.Lock()
_senders: dict = {}  # credential id -> (revision, sender)

# Tests replace this to inject fakes: factory(credential) -> sender
factory = None


def _build(credential: Credential):
    if factory is not None:
        return factory(credential)
    secret = credential.get_secret()
    return FcmSender(secret) if credential.provider == Credential.FCM else ApnsSender(secret)


def sender_for(credential: Credential):
    with _lock:
        cached = _senders.get(credential.id)
        if cached and cached[0] == credential.revision:
            return cached[1]
        if cached:
            try:
                cached[1].close()
            except Exception:
                pass
        sender = _build(credential)
        _senders[credential.id] = (credential.revision, sender)
        return sender


def reset():
    with _lock:
        for _, sender in _senders.values():
            try:
                sender.close()
            except Exception:
                pass
        _senders.clear()
