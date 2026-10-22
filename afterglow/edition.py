"""Edition capabilities. Distribution builds replace only the two constants below.

The development source includes Pro. Free archives omit the environment engine;
this marker chooses the matching registration and object-only API behavior.
"""

IS_PRO = True
NAME = "Afterglow Pro"


def require_pro():
    if not IS_PRO:
        raise ValueError("Environment generation is included in Afterglow Pro")


def style_scope(scope):
    """Resolve legacy complete-look calls safely for the installed edition."""
    if not isinstance(scope, str) or scope not in ("ALL", "OBJECT", "EFFECTS"):
        raise ValueError("Choose ALL, OBJECT or EFFECTS style scope")
    if scope == "EFFECTS":
        require_pro()
    return "OBJECT" if not IS_PRO and scope == "ALL" else scope
