"""Import and dispatch witness for the additive island-set capability."""


def test_island_set_entrypoint_is_installed_without_replacing_single_deck():
    from earcrate.app import EarcrateCore

    assert callable(getattr(EarcrateCore, "propose_island_set", None))
    assert callable(getattr(EarcrateCore, "_single_deck_render_mashup", None))
    assert getattr(EarcrateCore, "_island_render_installed", False) is True
    assert EarcrateCore.__dict__["render_mashup"] is not EarcrateCore.__dict__["_single_deck_render_mashup"]
