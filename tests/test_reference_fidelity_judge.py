from earcrate.judge.audio import reference_fidelity_gates


def test_reference_fidelity_is_additive_for_dark_producer_reference():
    reference = {
        "rms_std_db": 4.588,
        "silence_ratio": 0.0233,
        "low200_share": 0.5848,
        "distinct_pcs": 2,
        "bpm": 92.285,
    }
    render = {
        "rms_std_db": 4.695,
        "silence_ratio": 0.0329,
        "low200_share": 0.6368,
        "distinct_pcs": 6,
        "bpm": 92.285,
    }

    result = reference_fidelity_gates(render, reference)

    assert result["passed"] is True
    assert all(result["gates"].values())
    assert reference["low200_share"] > 0.45
    assert "never replaces or weakens" in result["rule"]


def test_reference_fidelity_rejects_large_spectral_and_tempo_departure():
    reference = {
        "rms_std_db": 4.5,
        "silence_ratio": 0.03,
        "low200_share": 0.58,
        "distinct_pcs": 5,
        "bpm": 92.0,
    }
    render = {
        "rms_std_db": 1.0,
        "silence_ratio": 0.30,
        "low200_share": 0.20,
        "distinct_pcs": 1,
        "bpm": 120.0,
    }

    result = reference_fidelity_gates(render, reference)

    assert result["passed"] is False
    assert not result["gates"]["rms_std_delta"]
    assert not result["gates"]["low200_share_delta"]
    assert not result["gates"]["bpm_delta"]
