# Heritage map — the v0.5–v0.6 tricks and where they live in the rebuild

The point of this file: the mechanisms mastered in the Jukebreaker GT era
(v0.5.x deck discipline, v0.6.x fail-fast harvest / turnover / keyless
percussion) are the reason the old builds "spit out solid tracks" WITHOUT stem
separation — pure full-mix, old-school track mixing. The rebuild must not lose
them under a reskin (it already almost happened once: v0.8.20 "restore the
buffalo the LATTICE reskin dropped"). Each row names the trick, where it lives
now, and which executable gate protects it. A row with no gate is a known gap,
listed at the bottom.

Verified against the code at v0.8.26 (audit 2026-07-12).

## Alive and protected

| Trick (origin) | Where it lives now | Gate |
| --- | --- | --- |
| Varispeed-only deck discipline; role transform budgets (vocals ±2 st / ≤5 % stretch, drums ±1 st / ≤6 %) (v0.5.8) | `earcrate/deck/transform.py` — `drydeck_transform_limits`, `plan_varispeed_transform`; synthetic pitch costs ~10× varispeed in `_artifact_cost` | `test_budget_knob_bites` |
| BPM lattice — score candidate deck speeds, pick the playable deck BEFORE composing (v0.5.13/14, v0.6.1) | `earcrate/deck/lattice.py` — `score_bpm_lattice`; feasibility-first BPM choice in `compose_taste_arrangement` | `test_lattice_prefers_cleaner_speed` |
| Tempo-octave folding — half/double-time analyzer disagreements don't destroy valid tempo islands (v0.6.1) | `earcrate/deck/transform.py` — `fold_bpm_to_target` | covered inside lattice/budget gates |
| Key discipline / nearest-harmonic shift (v0.5.x) | `earcrate/deck/harmony.py` + `nearest_harmonic_shift` — with the banked lesson about the import bug that once silently disabled it | `test_budget_knob_bites` (key-equal probe), `test_percussion_is_keyless_but_vocals_are_not` |
| Keyless percussion — drum material is never key-gated (v0.6.x) | `earcrate/deck/transform.py` (percussive roles skip key) | `test_percussion_is_keyless_but_vocals_are_not` |
| Multideck tail overlay — sections are live decks; outgoing tails overhang, incoming downbeat stays on grid; up to 4 aux decks (v0.5.5/17) | `render_mashup` in `earcrate/app.py` — `select_tail_decks`, `blend_decks`, per-type tail pruning; report stamps `deck_model: v0.5.17` (the engine is the transplant, verbatim) | **gap — see below** |
| Six-transition DJ grammar: beatmatch_blend, bass_swap, acapella_bridge, impact_drop, hard_cut_pickup, hard_cut_to_air — each with distinct curve/bass policy, bass_swap does a real low-band owner split (v0.5.4/5) | planned in `plan_transition` (`earcrate/app.py`), consumed in render via `dj_bass_swap_blend` etc. (`earcrate/deck/dsp.py`) | audit counts named transitions (`score_arrangement`); **applied-in-render gap below** |
| Turnover contract / Girl Talk density — new element ~11 s, 15–25 sources, source-share caps, hard rotation while turnover unmet (v0.5.14, v0.6.4) | **upgraded to data**: `profiles/girl_talk_v1.json` via TasteSpec (`earcrate/tastespec/profiles.py`); same machinery drives `notorious_v1`, `troubadour_v1` | `test_girl_talk_ranking`, `test_taste_duration_and_vocal_count`, `test_curation_steers_composer` |
| Arrangement preflight + intent scoring — reject structurally empty plans; realized chaos/drama/whiplash/vocal scored against targets (v0.5.14/16, v0.6.1) | `score_arrangement` audit in `earcrate/app.py` (named transitions, false blends, covered-bar ratio, role leaks, predicted silence) | `test_intent_flips_winner`, `test_readiness_honest_on_40_random` |
| Fail-fast batched harvest (v0.6.2) | harvest path in `earcrate/app.py` | exercised by persona/curation gates |
| Post-render quality gate — degraded audio errors instead of presenting as success (v0.5.8/9) | `render_mashup` quality gate; `list_renders` surfaces `quality_gate_passed` | exercised in selftest |

## Deliberately cut — decisions, not accidents

- **Two-world / album-collision arranger** (v0.6.0; removed v0.8.0 "one
  composer"). The TasteSpec rails (floor / foreground / spark) cover the ROLE
  separation, but the pairing concept — voice world from one source cluster,
  bed world from another — has no equivalent. If it returns, it returns as a
  TasteSpec constraint (a profile can demand cross-cluster pairing), not as a
  second arranger. Open decision.
- **Floor-safe rescue** (v0.5.9/16). The masthead now says "no fallback render
  is allowed": where the old engine retreated to a conservative one-deck mix
  and still produced audio, the rebuild refuses. Some remembered "solid
  tracks" were rescue output. Kept dead on purpose — rescue created false
  success states — but the refusals it causes are expected behavior, not bugs.

## Known gaps (missing gates, honestly listed)

1. **Applied-transition gate.** Nothing asserts that each of the six
   transition types renders with `applied: true` and the right tail-deck
   behavior on a fixture render. The audit counts *planned* types; the render
   path is exercised but not per-type asserted. This is the strongest missing
   protection for the tail-overlay trick.
2. **Tail-deck selection unit gate.** `select_tail_decks` pruning rules
   (bass_swap carries low+rhythm, hook blends carry the dry floor) are
   changelog-documented but not pinned by a test.

Do not delete a row from this table without either a CHANGELOG entry stating
the cut and why, or a gate proving the replacement covers it.
