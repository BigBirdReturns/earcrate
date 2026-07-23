import numpy as np

from earcrate.judge.audio import stable_presence_restore


def test_pretty_lights_finish_does_not_chase_girl_talk_treble_floor():
    """A persona-valid warm mix must not be brightened toward Girl Talk."""
    sr = 22050
    t = np.arange(sr * 3, dtype=np.float32) / sr
    # Warm program material with a little real presence, above the Pretty Lights
    # floor but below the Girl Talk finishing target.
    y = (0.34 * np.sin(2 * np.pi * 110 * t)
         + 0.22 * np.sin(2 * np.pi * 700 * t)
         + 0.11 * np.sin(2 * np.pi * 5200 * t)).astype(np.float32)
    pretty_lights = {
        "rms_std_db": {"target": 4.5, "floor": 3.0},
        "low200_share": {"ceiling_fail": 0.50, "ceiling_warn": 0.38, "floor_warn": 0.08},
        "high3000_share": {"target": 0.12, "floor_warn": 0.07, "floor_fail": 0.03},
    }

    out, receipt = stable_presence_restore(
        y, sr, return_receipt=True, spectral_profile=pretty_lights
    )

    assert receipt["spectral_profile"] == "persona"
    assert receipt["passed"] is True
    assert receipt["high_boost_db"] == 0.0
    assert np.isfinite(out).all()
