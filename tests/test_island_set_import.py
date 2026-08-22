"""Import and dispatch witnesses for the additive island-set capability."""


def test_island_set_entrypoint_is_installed_without_replacing_single_deck():
    from earcrate.app import EarcrateCore

    assert callable(getattr(EarcrateCore, "propose_island_set", None))
    assert callable(getattr(EarcrateCore, "_single_deck_render_mashup", None))
    assert getattr(EarcrateCore, "_island_render_installed", False) is True
    assert EarcrateCore.__dict__["render_mashup"] is not EarcrateCore.__dict__["_single_deck_render_mashup"]


def test_island_dispatch_preserves_raw_renderer_introspection():
    """Installing the dispatcher must not erase the original decorator contract.

    Existing repository gates inspect render_mashup.__wrapped__ to verify that
    post-render refusal precedes WAV publication and that only full-PCM source
    identities reach the renderer. The island dispatcher must preserve that exact
    raw function rather than expose an undecorated proxy or an intermediate wrapper.
    """
    import inspect
    from earcrate.app import EarcrateCore

    raw = EarcrateCore.render_mashup.__wrapped__
    source = inspect.getsource(raw)
    assert '"failure_kind": "post_render_quality_gate"' in source
    assert "CASE WHEN f.audio_sha256_scope='full'" in source
    assert "run Analyze before stem separation" in source
