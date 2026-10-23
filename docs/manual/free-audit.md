# Noctune Free manual source audit

Content: `free.json`; verified against Free 0.29.1 in `/workspace/afterglow-audioreact`.

| Manual section | Source checked | Covered features |
|---|---|---|
| Install / quick start | `ui.py`, manifest, `easy.py`, `workflow.py` | Blender 5.2+, install ZIP, N sidebar, selection, automatic emission preparation, audio formats, look/build/update, preview |
| Objects and lights | `core.py`, `prepare.py`, `light_reaction.py`, `workflow.py` | Mesh/curve/text, Point/Spot/Area/Sun, direct collection vs Use Selected, shared data isolation, linked-object limits |
| Object looks | `presets.py`, `ui.py` | Five exact object-look names and palettes; main Build/Update reapplies built-in look |
| Ripple / meter | `props.py`, `ui.py`, `core.py` | Tempo-following sweep, beats, longest/X/Y/Z, Empty/Origin/center, Reverse, surface travel, Fast Playback, meter axis/invert and mutual exclusion |
| Object controls | `ui.py::draw_object`, nodes and light reaction | Frequency, band lock, Gain/Threshold, Decay, Delay, Intensity, Glow, colors, light Base Power |
| Saved looks | `creator.py`, `lookbook.py` | Save/apply/build/import/export/remove, first reacting object style, 64 snapshots, JSON portability, object-only exchange |
| Glow | `creator.py::draw_finish`, `ui.py::draw_advanced`, `finish.py` | Manual glow, automatic Free toggle, strength/threshold, compositing, independent removal |
| Timing and playback | `workflow.py::create_reaction`, `core.py::calibrate/setup_playback`, `bake.py`, `ui.py` | Automatic full-track calibration, authored-animation conflicts, Start Frame, Fast Playback, all manual operations, stale playback, audio sync |
| Render / cleanup | `core.py::remove`, `finish.py` | Blender rendering, native output/range preservation, material/light restoration, reassignment preservation, separate glow cleanup |
| Help / editions | README, actual edition/format checks | Common fixes, one edition at a time, upgrade, GPL and legacy Afterglow ID |

## Accurate edge cases retained

- Main Build/Update Reaction reapplies the selected built-in Object Look. Keep custom styling with Build Object Reaction Only (which recalibrates), Refresh Playback, or saved-look Apply & Build as appropriate.
- Free 0.29.1 accepts object-only look format 1, and older complete formats 1–3. Current Pro complete/environment format 6 is unsupported in this Free version: export an object-only look for exchange.
- Free's automatic-glow control lives in Timing & Glow; Pro's current UI moved it to Glow. `pro_body` supplies the alternate wording.
- Pro exposes Track and advanced Start Frame in one shared Music box. `pro_body` supplies the current Pro location without inventing a repeated music control.
- Through Objects needs Fast Playback; Level Meter is off while Through Objects is on; lights react at object origins and have no geometry meter.
- Calibrate normalizes audio response, not scene exposure, material strength or native light Base Power.

No addon source or runtime behavior was changed. This audit is maintenance material; it is not part of the beginner-facing HTML prose.
