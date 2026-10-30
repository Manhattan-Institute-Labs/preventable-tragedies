"""Outlets and the publication rule: an incident is published only when two
or more distinct outlets report it, or its primary source is on the trusted
list. The rule is ../database/pipeline/shared.py's, so both harvesters'
records are held to the same one."""

from config import repeat_offender_module

_shared = repeat_offender_module("shared")
host = _shared.host
outlets = _shared.outlets
is_trusted = _shared.is_trusted
publishable = _shared.publishable
