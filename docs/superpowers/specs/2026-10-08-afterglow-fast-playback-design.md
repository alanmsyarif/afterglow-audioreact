# Afterglow — Fast Playback (addendum)

Date: 2026-10-08
Status: design approved in chat; awaiting spec review
Extends: `2026-10-08-afterglow-design.md`, `2026-10-08-afterglow-ripple-design.md`

## Problem (measured 2026-10-08, Blender 5.2, user's 183 s song)

- One evaluation of `Sample Sound Frequencies` costs 35 ms on a 3 s clip and 140 ms on the
  183 s song, regardless of object count, vertex count or smoothing samples. Objects
  evaluate in parallel, so a scene plays at about 7–15 fps no matter how it's set up.
- Larger FFT sizes are *faster*, and the cost scales with song length. The cost is inside the
  node (it appears to re-read or analyse the sound on every evaluation), so Afterglow can't
  tune it away.

## Goal

Playback that doesn't evaluate the node at all, and that looks the same as live evaluation
within a few percent.

## Verified facts

- A GN `Switch` (FLOAT) whose `Switch` input is True skips the unused Sample Sound branch:
  0.12 ms/frame, against 62 ms live.
- Modifier inputs can be keyframed. The data path is
  `modifiers["<name>"].properties.inputs.<identifier>.value`.
- numpy reproduces the node's amplitude as follows: mix the channels to mono, take a
  4096-sample Hann window centred on the time, then `sum(|rfft|[low ≤ f ≤ high]) * 2 / 4096`.
  This gives correlation 0.9992 and a scale ratio of 0.985 (spread 3.7%) on the user's song.

## Design

### Node group v4

- Two new inputs, appended:
  - `Use Baked` (Bool, scene-wide)
  - `Baked Peak` (Float, per object, keyframed by the bake)
- `peak = Switch(Use Baked, live Repeat-Zone peak, Baked Peak)`. Threshold, Gain, Ripple
  Gain and Ripple Threshold, colors and attributes stay downstream and remain live.
- Migration as before: modifiers on v1–v3 groups move to the v4 group and keep their values.

### `bake.py` (new)

- `band_series(mono, rate, bands) -> (times, array[len(bands), hops])` computes the band
  amplitude at hop centres every 256 samples.
  - It uses the verified formula above.
  - The STFT is chunked, and a cumulative sum over bins gives every band in one pass.
- `bake_all(scene) -> int`, for every object with an Afterglow modifier:
  - The band is (Low, High), or the kick band (40, 150) while Ripple is on.
  - For each scene frame `f` from Start Frame − 1 to the song end + 2 s:
    `t = (f - Start)/fps - Delay - Ripple * Position * BeatsPerSweep * BeatLength(f)`.
    BeatLength comes from the group's Beat Length F-curve, or the node default.
  - It applies the same peak-hold as the node: `max over i<8 of amp(t - i*Decay/4) *
    exp(-i*(Decay/4)/max(Decay,1e-4))`, with `amp = 0` when the sample time is below 0.
    Amplitude is linearly interpolated between hops.
  - It replaces the object's `Baked Peak` F-curve with one key per frame
    (`keyframe_points.add` + `foreach_set`).
  - It stores a signature of the baked inputs on the object.
  - It raises `ValueError` when the sound is missing or unreadable.
- `outdated(scene) -> [objects]` lists objects whose current inputs no longer match their
  stored signature. The signature covers Low, High, Decay, Delay, Ripple, Ripple Position,
  Beats per Sweep, Start Frame and the sound path.
- Removing a bake deletes only Afterglow's `Baked Peak` F-curve. The object's other animation
  stays.

### Wiring

- New scene property `fast_playback` (Bool).
  - Its update runs sync, which writes `Use Baked` to every modifier, and bakes when turned on.
- While Fast Playback is on, these re-bake automatically: Apply, Re-spread, Sound, Start Frame,
  Ripple, and Beats per Sweep. Calibrate doesn't need to, because Gain and Threshold are
  downstream.
- New operator `afterglow.bake` ("Re-bake"). It reports "Baked N objects in X s".
- Panel: a Fast Playback checkbox. When `outdated()` isn't empty, it shows a warning row
  "Bake outdated (N objects)" with a Re-bake button.
- Remove also deletes the Baked Peak F-curves.

## Error handling

| Case | Behavior |
|---|---|
| Sound missing on disk while baking | `ValueError`; the operator or toggle reports it, and modifiers keep their previous bake |
| Baked settings edited in the modifier panel | Panel shows "Bake outdated", one click re-bakes |
| User's own object animation | Untouched by bake and Remove |
| Frames before Start Frame | Key at Start Frame − 1 is 0, extrapolated constant, so dark |

## Known limits

- Hand edits to the Beat Length keys aren't part of the signature. Re-bake after editing them.
- Baked values are sampled once per frame. Sub-frame motion blur interpolates between keys.

## Testing (`tests/test_bake.py` plus updates)

- On the tone file, numpy `band_series` matches the live node within 5% (or 0.02 absolute) at
  the bass, mid and treble frames.
- Baked levels match live levels on the three stage objects at several frames:
  - with Ripple off;
  - with Ripple on and positions spread.
- With Fast Playback on, playback over 30 frames is at least 20× faster than live.
- Changing Decay after baking lists the object in `outdated()`. Re-bake clears it.
- Remove deletes the bake F-curve and keeps the user's location keyframes.
- With Start Frame 25, frame 10 reads level 0 when baked.
- The UI toggle sets `Use Baked` on every modifier and creates the bake F-curves.
