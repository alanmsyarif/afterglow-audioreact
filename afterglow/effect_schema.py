"""Stable environment identifiers for portable looks and existing project data.

Shared with Free for importing object styles from Pro looks and retaining audio
owned by existing environments. This module contains no effect generator.
"""

TAG = "afterglow_effects"
EMITTER_TAG = "afterglow_emitter"
LAYERS = ("strikes", "blooms", "flashes", "trails", "downward", "gun", "signals", "lasers")
BAND_ITEMS = (
    ("LOW", "Low (20-150 Hz)", "Kicks and bass"),
    ("MID", "Mid (150-2k Hz)", "Melody and voices"),
    ("HIGH", "High (2k-16k Hz)", "Hats and air"),
    ("FULL", "Full range", "Everything at once"),
)
