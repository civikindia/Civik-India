"""Shared UTC clock helpers."""
from datetime import datetime, timezone


def utc_now():
    """Return a naive UTC datetime suitable for existing database columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_epoch_seconds():
    """Return the current UTC epoch timestamp as whole seconds."""
    return int(datetime.now(timezone.utc).timestamp())
