"""Branded primitive values used at the source boundary."""

from typing import NewType

DUID = NewType("DUID", str)
"""A decimal KW notice identifier."""

SourceURL = NewType("SourceURL", str)
"""A source URL constructed for the allowlisted KW notice board."""
