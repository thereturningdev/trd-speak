"""Functional tests for the 'Open Dictionary File…' menu action.

The bug: the row did nothing because it used the legacy
selectFile:inFileViewerRootedAtPath:, which selects the file but does not
activate Finder. The fix reveals via activateFileViewerSelectingURLs: (which
activates Finder and takes an NSArray of NSURL). These tests drive the real
handler with a fake NSWorkspace and assert the right call with the right URL.
"""

import types

import pytest

from flow import menubar


class _RecordingWorkspace:
    def __init__(self):
        self.revealed = []  # lists of NSURL passed to activateFileViewerSelectingURLs_
        self.opened = []    # NSURL passed to openURL_

    def activateFileViewerSelectingURLs_(self, urls):
        self.revealed.append(list(urls))

    def openURL_(self, url):
        self.opened.append(url)
        return True


@pytest.fixture
def patched(monkeypatch):
    rec = _RecordingWorkspace()
    monkeypatch.setattr(
        menubar.AppKit, "NSWorkspace",
        types.SimpleNamespace(sharedWorkspace=lambda: rec),
    )
    delegate = menubar._Delegate.alloc().init()
    return delegate, rec


def test_reveals_existing_dictionary_file_in_finder(patched, monkeypatch, tmp_path):
    delegate, rec = patched
    f = tmp_path / "dictionary.json"
    f.write_text('{"vocabulary": [], "replacements": []}')
    monkeypatch.setattr(menubar.paths, "DICTIONARY_PATH", f)

    delegate.openDictionaryFile_(None)

    # Reveal-in-Finder (activate + select the file), NOT the silent legacy call.
    assert len(rec.revealed) == 1
    urls = rec.revealed[0]
    assert len(urls) == 1
    assert str(urls[0].path()) == str(f)
    assert rec.opened == []  # the file exists, so we reveal, not open-folder


def test_opens_parent_folder_when_file_missing(patched, monkeypatch, tmp_path):
    delegate, rec = patched
    f = tmp_path / "nope" / "dictionary.json"  # parent dir need not exist for the call
    monkeypatch.setattr(menubar.paths, "DICTIONARY_PATH", f)

    delegate.openDictionaryFile_(None)

    # No file to select -> open the parent folder so the row still does something.
    assert rec.revealed == []
    assert len(rec.opened) == 1
    assert str(rec.opened[0].path()) == str(f.parent)


def test_does_not_use_the_legacy_selectfile_api(patched, monkeypatch, tmp_path):
    """Guard the regression: the dead path was selectFile:..., which our fake
    workspace does not even implement — so calling it would raise, not no-op."""
    delegate, rec = patched
    f = tmp_path / "dictionary.json"
    f.write_text("{}")
    monkeypatch.setattr(menubar.paths, "DICTIONARY_PATH", f)

    # Would raise AttributeError if the handler reached selectFile_…; it must not.
    delegate.openDictionaryFile_(None)
    assert rec.revealed and not rec.opened
