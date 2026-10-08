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
# s the config flow waits for the full status (for the scan). After a pause of ~10 min or
# more the converter hands over a backlog of buffered polls and the panel resumes its
# status telegrams only after ~13 s (seen twice on 2026-10-07); after a short pause ~3.4 s.
CONNECT_WAIT = 30.0
# s the setup waits until the panel talks at all. The full status may take longer (see
# above); the entities stay unavailable until it is there, instead of failing the setup
# and starting over with a new connection.
TALK_WAIT = 10.0
