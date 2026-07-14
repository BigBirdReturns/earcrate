#!/usr/bin/env python3
"""Generate the remix-persona ROSTER from the scouted producers.

Each spec below is a COMPACT style fingerprint derived from the producer's
heavily-documented signature (WhoSampled depth, Tracklib/RBMA/Micro-Chop
breakdowns, interviews) plus established production facts. Numbers (tempo,
density, spectral targets) are informed ESTIMATES -- calibration starting points
meant to be tuned on the box against real audio -- not measurements. Every spec
goes through the gated build_remix_persona(), which fills all load-bearing
structural fields from canonical values, so no persona here can be invalid.

Run: python scripts/build_roster.py  (writes profiles/remix_*.json)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earcrate.tastespec.remix_builder import build_remix_persona  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "profiles"

# archetype spectral/density presets (high3000 = air/brightness, low200 = low weight)
WARM_SOULCHOP = dict(  # dusty boom-bap soul-chop
    low200_ceiling_fail=0.48, low200_ceiling_warn=0.36, low200_floor_warn=0.08,
    high3000_target=0.13, high3000_floor_warn=0.07, high3000_floor_fail=0.04,
    rms_target=4.5, rms_floor=3.0, min_layers=3, max_layers=6,
    seconds_per_event=9.0, sources_per_minute=3.5, groove_feel="swung_boom_bap",
    groove_swing="heavy_16th", groove_syncopation="medium")
CINEMATIC_COLLAGE = dict(  # plunderphonic, dynamic, brighter
    low200_ceiling_fail=0.46, low200_ceiling_warn=0.35, low200_floor_warn=0.07,
    high3000_target=0.16, high3000_floor_warn=0.09, high3000_floor_fail=0.05,
    rms_target=5.0, rms_floor=3.4, min_layers=4, max_layers=6,
    seconds_per_event=7.0, sources_per_minute=4.5, groove_feel="sampled_break",
    groove_swing="moderate", groove_syncopation="high")
SUNLIT_POP = dict(  # bright maximalist pop collage
    low200_ceiling_fail=0.44, low200_ceiling_warn=0.34, low200_floor_warn=0.06,
    high3000_target=0.20, high3000_floor_warn=0.12, high3000_floor_fail=0.07,
    rms_target=4.5, rms_floor=3.2, min_layers=4, max_layers=6,
    seconds_per_event=6.0, sources_per_minute=5.0, groove_feel="four_on_floor",
    groove_swing="light", groove_syncopation="medium")
CLOUD_HAZE = dict(  # reverb-drenched, dark, sparse
    low200_ceiling_fail=0.50, low200_ceiling_warn=0.38, low200_floor_warn=0.08,
    high3000_target=0.10, high3000_floor_warn=0.05, high3000_floor_fail=0.03,
    rms_target=4.0, rms_floor=2.8, min_layers=3, max_layers=5,
    seconds_per_event=11.0, sources_per_minute=2.5, groove_feel="half_time_trap",
    groove_swing="loose", groove_syncopation="low")
DARK_808 = dict(  # trap, mono 808, melodic foreground
    low200_ceiling_fail=0.52, low200_ceiling_warn=0.40, low200_floor_warn=0.10,
    high3000_target=0.12, high3000_floor_warn=0.07, high3000_floor_fail=0.04,
    rms_target=4.5, rms_floor=3.0, min_layers=2, max_layers=5,
    seconds_per_event=10.0, sources_per_minute=3.0, groove_feel="half_time_trap",
    groove_swing="moderate", groove_syncopation="hats")
MAXIMAL_FUTURE = dict(  # future-beats, bright, big
    low200_ceiling_fail=0.50, low200_ceiling_warn=0.38, low200_floor_warn=0.08,
    high3000_target=0.19, high3000_floor_warn=0.11, high3000_floor_fail=0.06,
    rms_target=5.0, rms_floor=3.4, min_layers=4, max_layers=6,
    seconds_per_event=7.0, sources_per_minute=4.0, groove_feel="trap_festival",
    groove_swing="moderate", groove_syncopation="high")


def spec(pid, name, contract, basis, bpm, extra):
    s = dict(id=pid, name=name, contract=contract,
             provenance={"basis": basis, "scout": "roster fan-out (documented breakdowns)",
                         "note": "spectral/tempo/density are informed estimates, tune on the box"},
             bpm_low=bpm[0], bpm_high=bpm[1], half_time_feel=extra.get("_half", True),
             source_seconds=extra.get("_ss", 12.0))
    s.update({k: v for k, v in extra.items() if not k.startswith("_")})
    return s


ROSTER = [
    spec("remix_dilla_v1", "Remix — J Dilla v1",
         "un-quantized soul micro-chops with drunk off-grid MPC swing under a foreground hook",
         "J Dilla / Donuts: structural soul micro-chops, humanized off-grid groove (samplingdonuts, Tracklib, Sewell typology)",
         (85, 95), dict(WARM_SOULCHOP, groove_swing="drunk_off_grid")),
    spec("remix_madlib_v1", "Remix — Madlib v1",
         "dusty loose loops from obscure global crates (jazz/bossa/Bollywood) with SP-303 grit",
         "Madlib / Madvillainy: crate-dig loop flips, SP-303 (Tracklib 'Sample Like Madlib', RBMA)",
         (85, 95), dict(WARM_SOULCHOP, low200_ceiling_warn=0.37)),
    spec("remix_shadow_v1", "Remix — DJ Shadow v1",
         "cinematic vinyl collage: moody layered breaks and found-sound built into a foreground arc",
         "DJ Shadow / Endtroducing: 100% sample MPC60 collage (DJ Mag masterclass, Micro-Chop)",
         (90, 100), CINEMATIC_COLLAGE),
    spec("remix_avalanches_v1", "Remix — The Avalanches v1",
         "sunlit seamless multi-source collage assembled into a new pop song under a lead melody",
         "The Avalanches / Since I Left You: maximalist plunderphonics (WhoSampled tree, clearance history)",
         (100, 120), SUNLIT_POP),
    spec("remix_dangermouse_v1", "Remix — Danger Mouse v1",
         "one a cappella foreground rebuilt over a single-source instrumental bed",
         "Danger Mouse / The Grey Album: Jay-Z acapellas over Beatles beds (Micro-Chop, track-by-track)",
         (85, 100), dict(WARM_SOULCHOP, high3000_target=0.15, foreground_max_share=0.6)),
    spec("remix_kanye_v1", "Remix — Kanye (chipmunk soul) v1",
         "sped-up pitched soul vocal loop as the hook with drums built around it",
         "Kanye 'chipmunk soul': pitched/sped soul loops as lead (RBMA, Loop Kitchen, WhoSampled)",
         (85, 100), dict(WARM_SOULCHOP, high3000_target=0.18, high3000_floor_warn=0.10)),
    spec("remix_flyinglotus_v1", "Remix — Flying Lotus v1",
         "woozy sidechained jazz-glitch beds under off-kilter percussion",
         "Flying Lotus: Dilla-inspired off-grid drums, sidechain, warped jazz layering (Ableton, RBMA)",
         (80, 110), dict(CINEMATIC_COLLAGE, high3000_target=0.14, groove_feel="off_kilter_glitch")),
    spec("remix_kaytranada_v1", "Remix — Kaytranada v1",
         "swung off-beat sample chops at house tempo, drums dropping on the 3, golden-era palette",
         "Kaytranada: off-beat 16th chops, signature swing (Liveschool, Tracklib, Switched On Pop)",
         (100, 115), dict(WARM_SOULCHOP, high3000_target=0.16, groove_feel="swung_house",
                          _half=False, min_layers=3, max_layers=6)),
    spec("remix_clamscasino_v1", "Remix — Clams Casino v1",
         "blown-out reverbed vocal-sample bed under sparse trap drums",
         "Clams Casino: pitched reverb-drenched vocal loops (WhoSampled 100+, Instrumentals mixtapes)",
         (130, 150), CLOUD_HAZE),
    spec("remix_alchemist_v1", "Remix — The Alchemist v1",
         "hazy loop-based psych/soul flips with minimal drum treatment",
         "The Alchemist: loop-driven psych/soul flips (WhoSampled, Rhythm Roulette, Tracklib)",
         (85, 95), dict(WARM_SOULCHOP, seconds_per_event=11.0, max_layers=5)),
    spec("remix_premier_v1", "Remix — DJ Premier v1",
         "chopped jazz/soul loops with scratched-vocal hook choruses",
         "DJ Premier: fragmented-loop reassembly + scratch-hook choruses (WhoSampled, Tracklib)",
         (85, 95), dict(WARM_SOULCHOP, high3000_target=0.15)),
    spec("remix_nujabes_v1", "Remix — Nujabes v1",
         "warm modal-jazz loops over gentle boom-bap — the lo-fi/chillhop blueprint",
         "Nujabes: modal-jazz loop selection, emotive chopping (Tracklib 'Metaphorical Sampling')",
         (80, 92), dict(WARM_SOULCHOP, high3000_target=0.12, rms_target=4.0)),
    spec("remix_rjd2_v1", "Remix — RJD2 v1",
         "widescreen soul/funk collage with dramatic arrangement shifts",
         "RJD2 / Deadringer: dense multi-source cut-and-paste (WhoSampled, 'RJ's Originals')",
         (90, 100), CINEMATIC_COLLAGE),
    spec("remix_9thwonder_v1", "Remix — 9th Wonder v1",
         "filtered soul-loop chops with head-nod boom-bap drums",
         "9th Wonder: clean filtered soul-loop technique, FL Studio method (Tracklib, lectures)",
         (85, 95), dict(WARM_SOULCHOP, high3000_target=0.14)),
    spec("remix_knxwledge_v1", "Remix — Knxwledge v1",
         "off-grid soul-loop collages with hazy chopped drums",
         "Knxwledge: prolific collage-of-soul-loops over lo-fi drums (FADER Beat Construction, FACT)",
         (82, 92), dict(WARM_SOULCHOP, high3000_target=0.11, groove_swing="off_grid")),
    spec("remix_toddedwards_v1", "Remix — Todd Edwards v1",
         "hundreds of tiny retuned vocal micro-chops rebuilt into new melodies over garage drums",
         "Todd Edwards: codified micro-sampling into pre-written progressions (RBMA lecture)",
         (125, 135), dict(SUNLIT_POP, _half=False, seconds_per_event=5.0, sources_per_minute=6.0,
                          groove_feel="us_garage", groove_syncopation="high")),
    spec("remix_hudmo_v1", "Remix — Hudson Mohawke v1",
         "maximalist brass-stab fanfares and chopped vocal hits over trap drums",
         "Hudson Mohawke / TNGHT: maximalist sample-and-synth stacking (RA Art of Production, RBMA)",
         (130, 160), MAXIMAL_FUTURE),
    spec("remix_metroboomin_v1", "Remix — Metro Boomin v1",
         "haunting piano/bell melody foreground over distorted mono 808 low-end",
         "Metro Boomin: melodic-foreground-over-hard-808 formula (Passion of the Weiss, 808 breakdowns)",
         (130, 145), DARK_808),
    spec("remix_madeon_v1", "Remix — Madeon v1",
         "launchpad-triggered pop-sample fragments locked to a four-on-the-floor grid",
         "Madeon / Pop Culture: 39-song live Launchpad mashup, fully documented (Dismantled map, Wiki)",
         (125, 130), dict(SUNLIT_POP, _half=False, groove_feel="electro_house")),
    spec("remix_burial_v1", "Remix — Burial v1",
         "warped found R&B vocal lead over vinyl crackle, sub-bass and shuffled 2-step",
         "Burial: pitched/stretched acapellas over crackle beds, swung off-grid garage (Attack Mag, MusicTech)",
         (130, 138), dict(CLOUD_HAZE, low200_ceiling_fail=0.52, groove_feel="shuffled_2step",
                          groove_syncopation="high")),
]


def main():
    written = []
    for s in ROSTER:
        persona = build_remix_persona(s)
        path = PROFILES / f"{persona['id']}.json"
        path.write_text(json.dumps(persona, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(persona["id"])
    print(f"wrote {len(written)} personas:")
    for pid in written:
        print("  ", pid)


if __name__ == "__main__":
    main()
