"""End-to-end test: a learned correction is persisted and applied to the next dictation."""


def test_correction_is_learned_and_applied_next_time(monkeypatch, tmp_path):
    import flow.app as app_mod
    from flow.config import Config
    from flow.dictionary import load_dictionary

    monkeypatch.setattr(app_mod.paths, "DICTATIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(app_mod.paths, "DICTIONARY_PATH", tmp_path / "dict.json")
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_mod, "make_transcriber", lambda *a, **k: object())

    app = app_mod.App(Config())

    # User corrects an uncommon mishearing via the editor (original, edited).
    app.learn("call diotaleavy", "call Diotalevi")

    # Persisted with the learned flag.
    saved = load_dictionary(tmp_path / "dict.json")
    assert any(r.from_ == "diotaleavy" and r.to == "Diotalevi" and r.learned
               for r in saved.replacements)
    assert "Diotalevi" in saved.vocabulary

    # Applied to the next dictation.
    app.recorder = type("R", (), {"stop": lambda self: __import__("numpy").ones(16000, dtype="float32")})()
    app.transcriber = type("T", (), {"transcribe": lambda self, audio, hotwords=None: "meet diotaleavy"})()
    monkeypatch.setattr(app, "can_paste", lambda: True)
    monkeypatch.setattr(app.hotkey, "wait_all_released", lambda: True)
    pasted = []
    monkeypatch.setattr(app_mod, "paste_text", lambda s, **k: pasted.append(s))
    app._process()
    assert pasted == ["meet Diotalevi "]


def test_learn_does_not_raise_or_mutate_when_save_fails(monkeypatch, tmp_path):
    import flow.app as app_mod
    from flow.config import Config
    monkeypatch.setattr(app_mod.paths, "DICTATIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(app_mod.paths, "DICTIONARY_PATH", tmp_path / "dict.json")
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_mod, "make_transcriber", lambda *a, **k: object())
    app = app_mod.App(Config())
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(app_mod, "save_dictionary", boom)
    before_reps = list(app.dictionary.replacements)
    before_vocab = list(app.dictionary.vocabulary)
    app.learn("call diotaleavy", "call Diotalevi")  # must NOT raise
    assert app.dictionary.replacements == before_reps   # no mutation on failed save
    assert app.dictionary.vocabulary == before_vocab


def test_correction_hotkey_participates_in_suspend_resume(monkeypatch, tmp_path):
    import flow.app as app_mod
    from flow.config import Config
    monkeypatch.setattr(app_mod.paths, "DICTATIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(app_mod.paths, "DICTIONARY_PATH", tmp_path / "dict.json")
    calls = []
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: calls.append((id(self), "start")))
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: calls.append((id(self), "stop")))
    monkeypatch.setattr(app_mod, "make_transcriber", lambda *a, **k: object())
    app = app_mod.App(Config())
    cid = id(app.correction_hotkey)
    app.suspend_hotkeys()
    assert (cid, "stop") in calls
    app.resume_hotkeys()
    assert (cid, "start") in calls
