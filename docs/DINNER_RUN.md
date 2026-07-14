# DINNER RUN — autonomous "drop an album" brief for the box

The owner is out. Goal: **while they eat, render an album they can audition when
they're back**, and leave a committed trace of what happened. Fire-and-forget;
degrade gracefully; never wedge the UI.

## Do this, in order

1. **Sync**: `git fetch && git checkout claude/code-buddy-communication-bp04lk && git pull`.
   The launcher rebuilds `dist/earcrate.py` on start.

2. **Fresh analysis (required — ANALYZER_VERSION bumped to `gt-v0.7.0-beatstate`).**
   The per-beat `beat_state` is new, so the analysis cache repopulates on this
   pass. Analyze a solid slice (bigger is better for coverage), then build crates
   + graphs for all three personas:
   - `POST /api/analyze_bg` (or the analyze button), let it finish
   - `POST /api/ear_crate/build?force` and `POST /api/taste/graph` per persona

3. **(Optional, uses the idle GPU well) warm stems** so renders are cache-hits:
   `POST /api/stems/warm {"taste_profile":"girl_talk_v1"}` (repeat per persona).
   Companion caching now stores vocals+no_vocals from one pass.

4. **Render the album** (background job):
   `POST /api/album {"tracks":10, "target_seconds":150, "recognizability_bias":"max"}`
   or headless:
   ```python
   from earcrate.app import EarcrateCore
   core = EarcrateCore()                      # auto-configures from saved config
   print(core.render_album({"tracks":10, "target_seconds":150, "recognizability_bias":"max"}))
   ```
   It composes+renders across all three personas × many seeds, dedupes by
   arrangement, and writes:
   - `work/renders/album_<timestamp>/` — the WAVs + `README.md` (playlist) + `album_manifest.json`
   - `<agent_root>/ALBUM_REPORT.md` — a committable copy of the playlist

5. **Commit the trace back** (NOT the WAVs — too big; they stay local):
   ```
   git add docs/  # if you copy ALBUM_REPORT.md into docs/
   git commit -m "dinner run: album report" && git push
   ```
   Easiest: copy `<agent_root>/ALBUM_REPORT.md` to `docs/ALBUM_REPORT.md` and push
   it, so the cloud session + owner can see the tracklist, seeds, scores, and gate
   verdicts without the audio.

## Notes / latitude

- **Gate verdicts are advisory here.** The recalibrated spectral gate FLAGS
  presence-dark renders (real Girl Talk high3000 ~0.31 vs our ~0.05). The album
  KEEPS flagged tracks on purpose — they're for the owner's ear while the mix is
  fixed. Don't suppress them.
- **If you want gate-clean tracks**, the known lever is upstream: high-pass the
  instrumental beds at mix time + lift presence (see `gate_recalibration` and
  `patch2` notes in `AGENT_HANDOFF.json`). `stable_presence_restore` helps but a
  fixed master EQ can't fully rescue a 10×-dark mix — the bed high-pass is the
  real fix. Iterate freely and re-run the album if you land it.
- **You have latitude** to pick the strongest slice, tune `recognizability_bias`,
  vary `target_seconds`, or add seeds. The one hard requirement: leave a
  committed `ALBUM_REPORT.md` so there's a record when the owner checks.
- troubadour may skip on coverage (composition gap, not a bug) — that's fine; the
  album shows what each persona CAN make right now.
