from flow.menubar import _about_text, _make_version_item


def test_about_text_dev():
    assert _about_text("0.1.2", True) == "TRD Speak 0.1.2 (dev)"


def test_about_text_dist():
    assert _about_text("0.1.2", False) == "TRD Speak 0.1.2"


def test_version_item_dev_is_labelled_and_disabled():
    item = _make_version_item("0.1.2", True)
    # Always-visible info row, never an action: the version must show without a
    # click or a notification (banners are unreliable — see module docstring).
    assert str(item.title()) == "TRD Speak 0.1.2 (dev)"
    assert not item.isEnabled()
    assert item.action() is None


def test_version_item_dist_is_labelled_and_disabled():
    item = _make_version_item("0.1.2", False)
    assert str(item.title()) == "TRD Speak 0.1.2"
    assert not item.isEnabled()
    assert item.action() is None
