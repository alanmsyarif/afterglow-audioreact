# Afterglow — Tempo Ripple (addendum)

Date: 2026-10-08
Status: architecture approved in chat; awaiting spec review
Extends: `2026-10-08-afterglow-design.md`

## Goal

An optional mode where every beat sweeps across the collection as a wave. Each object fires a
little after the previous one, and the wave speeds up when the music's tempo speeds up.

## User decisions

- Ripple source: one shared beat. With Ripple on, all objects react to the kick band
  (40–150 Hz) instead of their own band.
- Order: by position, along the axis where the object origins spread the most.
- Sweep length: 1 beat by default. "Beats per Sweep" can change it.
- Sequencer auto-setup was a separate request and is already shipped (commit cdaa341).

## Verified facts (probed 2026-10-08, Blender 5.2)

- `aud.Sound(path).data()` returns a float32 numpy array of shape (samples, channels). For a
  3-minute stereo 48 kHz file it takes 0.07 s.
- A keyframed `ShaderNodeValue` inside the geometry node group is evaluated per frame for every
  object using the group.
- Prototype tempo detector: log spectral flux, then 8 s autocorrelation windows every 1 s,
  60–180 BPM, with a mild prior around 120 BPM.
  - On a synthetic click track accelerating from 120 to 160 BPM it tracks within about 1 BPM.
  - On the user's song it reads mostly 122 BPM with sections at 165 BPM and isolated outliers.
    A median filter removes the outliers.
- The analysis runs in 0.6 s for a 3-minute song.

## Architecture

### `tempo.py` (new)

- `tempo_curve(samples, rate) -> [(seconds, bpm)]`, sampled every 1 s.
  - Onset envelope: log-magnitude STFT (FFT 2048, hop 512, chunked so memory stays bounded),
    positive spectral flux, minus a 16-frame moving average, clipped at 0.
  - Each 8 s window is autocorrelated over the lags for 60–180 BPM, weighted by
    `exp(-0.5 * (log2(bpm/120) / 0.9)^2)`, and the argmax becomes that window's BPM.
  - A 5-point median filter is applied afterwards.
  - Audio shorter than about 0.2 s returns `[(duration/2, 120.0)]`.
- `bake(scene) -> curve`. It loads the scene Sound with `aud`, computes the curve, clears the
  group's animation, and keys the group's `Beat Length` Value node to `60 / bpm` at frame
  `Start Frame + t * fps`.
  - It raises `ValueError("Tempo analysis needs the sound file on disk")` if the file can't be read.

### Node group v2

- New inputs, appended so existing socket identifiers keep their values:
  - `Ripple` (Bool, scene-wide)
  - `Beats per Sweep` (Float, default 1.0, scene-wide)
  - `Ripple Position` (Float 0..1, per object, set by the addon)
- New internal Value node `Beat Length`, default 0.5 (120 BPM until baked).
- `Low' = Low + Ripple * (40 - Low)` and `High' = High + Ripple * (150 - High)`.
- `Delay' = Delay + Ripple * Ripple Position * Beats per Sweep * Beat Length(t)`.
- **Versioning.** The group tag holds a version number (`afterglow = 2`). `get_group()` finds
  groups with an older tag, points their modifiers at a fresh v2 group (keeping input values by
  identifier), deletes the old group and takes over its name. Files made with v0.2 keep working.

### Collection operations

- `place(objs)`: picks the axis with the largest spread of world-space origins and sets each
  object's `Ripple Position = (p - min) / span`. All objects get 0 if they coincide. This runs
  on Apply after respread.
- `sync` also writes `Ripple` and `Beats per Sweep`.

### Properties and UI

- Scene gets two new properties:
  - `ripple` (Bool). Its update runs sync, and also bake when turned on.
  - `beats_per_sweep` (Float, 0.05–16, default 1). Its update runs sync.
- When Sound changes while Ripple is on, the tempo is re-baked. A failure is ignored and the
  previous or default curve stays.
- The Apply operator always bakes after applying.
  - It reports the BPM range, e.g. "tempo 122–165 BPM".
  - A failed bake is reported as a warning ("Tempo analysis skipped: …") and Apply still succeeds.
- Panel: Ripple checkbox and Beats per Sweep field in the scene box. `Ripple Position` isn't
  shown, because it's computed.

## Error handling

| Case | Behavior |
|---|---|
| Sound packed or missing on disk | Bake is skipped with a warning; Beat Length keeps its default or previous curve |
| Very short audio | Single 120 BPM key |
| All objects at the same location | Position 0 for all, so they pulse together |
| File made with v0.2 (old group) | Migrated automatically on the next Apply or `get_group()` call, values kept |
| Wrong tempo section | Edit the `Beat Length` keys in the Graph Editor (group animation) |

## Known limits

- The Beat Length curve lives on the shared node group, so two scenes with different songs in
  one file share one curve.
- Tempo resolution is about 4 BPM at 160 BPM, due to lag quantization at hop 512 and 48 kHz.
  That's fine for wave speed.

## Testing

- `tests/test_tempo.py`, pure numpy plus bake:
  - Steady 100 BPM clicks read 100 ± 4 BPM in every window, with no octave errors.
  - Clicks accelerating from 120 to 160 BPM match the true local BPM within 5 in every window.
  - A single-window spike is removed by the median filter.
  - Audio shorter than 0.2 s gives the 120 BPM fallback.
  - Baking a 100 BPM click WAV keys Beat Length to about 0.6 s, and re-baking doesn't duplicate keys.
- `tests/test_nodes.py`:
  - With Ripple on, an object with a treble band still reacts to the 100 Hz tone, because it
    uses the kick band.
  - Position 1 lags position 0 by Beats per Sweep × Beat Length.
  - A v1-tagged group migrates and keeps a non-default Gain.
- `tests/test_core.py`:
  - `place` orders by location, not name, and picks the longest axis.
  - Objects at the same location all get 0.
  - Sync propagates Ripple and Beats per Sweep.
- `tests/test_ui.py`: the Apply operator leaves Beat Length animated, and the ripple toggle
  reaches the modifiers.
