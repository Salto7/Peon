"""Outbound notification channels: webhook / email / Telegram (Core 5.1).

Each channel is independently optional, off until its own setting is filled
in — nothing is required. Stdlib ``urllib`` for the two HTTP channels (no
new dependency); Django's own ``send_mail`` for email. Called off
``FindingStore.record``/``triage`` via ``tasks.enqueue_notification`` so a
slow/unreachable endpoint never delays the agent's tool call.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def _post_json(url: str, payload: dict[str, Any], *, timeout: float = 5.0) -> bool:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        return True
    except Exception as exc:  # noqa: BLE001 — a dead endpoint must never raise
        logger.warning("notification POST to %s failed: %s", url, exc)
        return False


def _send_webhook(text: str, *, extra: dict[str, Any] | None = None) -> bool:
    from peon.projects.runtime_settings import PeonSettings

    url = PeonSettings.get_str("NOTIFY_WEBHOOK_URL", "").strip()
    if not url:
        return False
    payload: dict[str, Any] = {"text": text}
    if extra:
        payload.update(extra)
    return _post_json(url, payload)


def _send_telegram(text: str) -> bool:
    from peon.projects.runtime_settings import PeonSettings

    token = PeonSettings.get_str("NOTIFY_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = PeonSettings.get_str("NOTIFY_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    return _post_json(url, {"chat_id": chat_id, "text": text[:4000]})


def _send_email(subject: str, text: str) -> bool:
    from peon.projects.runtime_settings import PeonSettings

    to = PeonSettings.get_str("NOTIFY_EMAIL_TO", "").strip()
    if not to:
        return False
    try:
        from django.conf import settings
        from django.core.mail import send_mail

        send_mail(
            subject,
            text,
            getattr(settings, "DEFAULT_FROM_EMAIL", "") or "peon@localhost",
            [to],
            fail_silently=False,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — no SMTP configured is routine, not fatal
        logger.warning("notification email failed: %s", exc)
        return False


def notify(subject: str, text: str, *, extra: dict[str, Any] | None = None) -> dict[str, bool]:
    """Fan out to every configured channel; one channel's failure never blocks another."""
    results: dict[str, bool] = {}
    for name, fn in (
        ("webhook", lambda: _send_webhook(text, extra=extra)),
        ("telegram", lambda: _send_telegram(f"{subject}\n{text}")),
        ("email", lambda: _send_email(subject, text)),
    ):
        try:
            results[name] = fn()
        except Exception:  # noqa: BLE001
            logger.warning("notification channel %s failed", name, exc_info=True)
            results[name] = False
    return results
