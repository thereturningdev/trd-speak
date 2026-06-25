"""Guard: the GUI window modules must be importable together in one process.

PyObjC class names are process-global; two modules defining the same NSObject
subclass name raise objc.error on the second import. The rest of the suite never
imports these together, so this test is the guard. (main.py -> menubar imports
both window modules, so a collision would crash the real app at launch.)
"""


def test_gui_modules_import_together():
    import flow.settings_window  # noqa: F401
    import flow.correction_window  # noqa: F401
    import flow.menubar  # noqa: F401
