"""Gateway (J533) notes.

The ID.3 OBD-II port is served by the central gateway ECU (J533), which
bridges the diagnostic lines to the vehicle's internal buses. Consequences:

  * Standard OBD-II modes (01/03/09) are answered by the gateway on behalf
    of the powertrain ECUs (functional 0x7DF requests).
  * VAG UDS requests are sent with an ECU-specific header (ATSH 7xx). Some
    ECUs answer directly; others may only respond when the gateway routes
    the request. Which is true per ECU must be determined experimentally
    (`python main.py discover`).
  * The gateway's own diagnostic address is NOT verified here, so it is not
    probed by default. Do not invent an address.

No DIDs are registered for the gateway at this stage.
"""
from __future__ import annotations  # noqa: F401
