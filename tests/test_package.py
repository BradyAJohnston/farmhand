import sys


def test_lazy_exports_do_not_import_modal_or_rich():
    for name in list(sys.modules):
        if name.startswith("farmhand") or name in ("modal", "rich"):
            del sys.modules[name]
    import farmhand

    assert "modal" not in sys.modules
    assert "rich" not in sys.modules
    assert farmhand.__version__
    assert "submit_render" in dir(farmhand)
    assert farmhand.combine_frames.__name__ == "combine_frames"
    assert farmhand.Config().app_name == "farmhand"
