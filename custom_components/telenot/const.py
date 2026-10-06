"""Constants for the Telenot integration."""

from __future__ import annotations

from typing import Final

from .client import Timing

DOMAIN: Final = "telenot"
MANUFACTURER: Final = "Telenot"
MODEL: Final = "complex 400"

DEFAULT_PORT: Final = 8234

CONF_DETECTORS: Final = "detectors"
CONF_CODE: Final = "code"
CONF_CODE_FOR: Final = "code_for"

# Modes that can be protected by the code. Default as before: only arming away needs it.
CODE_MODES: Final = ["arm_away", "arm_home", "disarm"]
DEFAULT_CODE_FOR: Final = ["arm_away"]

# Read at runtime (tests shorten them).
CLIENT_TIMING = Timing()
CONNECT_WAIT = 15.0  # s to wait for the panel when setting up
