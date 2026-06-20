from flow.menubar import _about_text


def test_about_text_dev():
    assert _about_text("0.1.2", True) == "TRD Speak 0.1.2 (dev)"


def test_about_text_dist():
    assert _about_text("0.1.2", False) == "TRD Speak 0.1.2"
