"""Edition capabilities. Distribution builds replace only the two constants below.

This repository contains Noctune Free. The environment engine lives in the
separate Pro repository; shared identifiers retain existing project compatibility.
"""

IS_PRO = False
NAME = "Noctune Free"


def require_pro():
    if not IS_PRO:
        raise ValueError("Environment generation is included in Noctune Pro")


def style_scope(scope):
    """Resolve legacy complete-look calls safely for the installed edition."""
    if not isinstance(scope, str) or scope not in ("ALL", "OBJECT", "EFFECTS"):
        raise ValueError("Choose ALL, OBJECT or EFFECTS style scope")
    if scope == "EFFECTS":
        require_pro()
    return "OBJECT" if not IS_PRO and scope == "ALL" else scope
