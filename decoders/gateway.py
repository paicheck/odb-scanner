"""Gateway (J533) notes.

The ID.3 OBD-II port is served by the central gateway ECU (J533), which
bridges the diagnostic lines to the vehicle's internal buses. Consequences:

  * Standard OBD-II modes (01/03/09) are answered by the gateway on behalf
    of the powertrain ECUs (functional requests).
  * UDS requests are also sent functionally (no ATSH -- see
    diagnostic/elm327.py set_header): whichever ECUs answer are identified
    by their response headers (18DAF1xx on this car's 29-bit bus). Which
    ECU answers which DID is determined experimentally
    (`python main.py discover`).
  * The gateway's own diagnostic address is NOT verified here, so it is not
    probed by default. Do not invent an address.

No DIDs are registered for the gateway at this stage.
"""
from __future__ import annotations  # noqa: F401
