"""Exceptions raised by cui_messaging instead of the underlying broker client errors."""


class BrokerDownError(Exception):
    """Raised when the broker cannot be reached or does not confirm a published message in time."""
