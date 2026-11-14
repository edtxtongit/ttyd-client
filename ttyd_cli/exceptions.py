"""
Custom exceptions for TTYD client.
"""


class InvalidAuthorization(Exception):
    """Raised when authentication fails or credentials are invalid."""


class AuthenticationRequired(InvalidAuthorization):
    """Raised only for an HTTP Basic authentication challenge."""
