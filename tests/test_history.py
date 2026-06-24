"""Disk-backed recent-dictations store.

The store persists each dictation to a per-build JSON file so the most recent
dictation can be re-pasted even after the app restarts (e.g. macOS only honors
a fresh Input Monitoring grant in a new process). It keeps at most
``MAX_HISTORY`` entries, newest last on disk / newest first via ``items()``.
"""

import threading

import pytest

from flow.history import MAX_HISTORY, History


@pytest.fixture
def store(tmp_path):
    """A History backed by a throwaway file (never the real user storage)."""
    return History(path=tmp_path / "dictations.json")


def test_add_then_items_returns_newest_first(store):
    store.add("first")
    store.add("second")
    store.add("third")
    assert store.items() == ["third", "second", "first"]


def test_latest_returns_the_most_recent(store):
    assert store.latest() is None
    store.add("first")
    store.add("second")
    assert store.latest() == "second"


def test_caps_at_max_history_and_evicts_oldest(store):
    total = MAX_HISTORY + 5
    for i in range(total):
        store.add(str(i))
    items = store.items()
    assert len(items) == MAX_HISTORY
    assert items == [str(i) for i in range(total - 1, total - 1 - MAX_HISTORY, -1)]


def test_clear_empties_history(store):
    store.add("a")
    store.add("b")
    store.clear()
    assert store.items() == []
    assert store.latest() is None


def test_persists_across_instances(tmp_path):
    """The reported recovery case: a dictation made before the app restarts
    must still be re-pastable by a fresh process reading the same file."""
    path = tmp_path / "dictations.json"
    first = History(path=path)
    first.add("written before restart")

    second = History(path=path)  # simulates a brand-new process
    assert second.latest() == "written before restart"
    assert second.items() == ["written before restart"]


def test_cap_is_enforced_across_instances(tmp_path):
    """Re-opening the file must not let it grow past the cap."""
    path = tmp_path / "dictations.json"
    for i in range(MAX_HISTORY + 3):
        History(path=path).add(str(i))  # a fresh instance each time
    assert len(History(path=path).items()) == MAX_HISTORY


def test_survives_unicode_and_newlines(store):
    weird = "café — naïve 𝓤𝓷𝓲𝓬𝓸𝓭𝓮\t漢字\nsecond line"
    store.add(weird)
    assert History(path=store._path).latest() == weird


def test_missing_file_reads_as_empty(tmp_path):
    h = History(path=tmp_path / "does-not-exist.json")
    assert h.items() == []
    assert h.latest() is None


def test_corrupt_file_is_ignored_not_raised(tmp_path):
    path = tmp_path / "dictations.json"
    path.write_text("{ this is not valid json ]")
    h = History(path=path)
    assert h.items() == []        # tolerated, never raises
    h.add("recovers")            # and a subsequent add still works
    assert h.latest() == "recovers"


def test_concurrent_adds_are_threadsafe(store):
    """Many threads adding at once must never lose or garble an entry."""
    threads_n = 8
    per_thread = 50
    start = threading.Barrier(threads_n)
    valid = {f"{t}-{n}" for t in range(threads_n) for n in range(per_thread)}

    def worker(tid):
        start.wait()  # release all threads together for maximum contention
        for n in range(per_thread):
            store.add(f"{tid}-{n}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
    for thr in threads:
        thr.start()
    for thr in threads:
        thr.join(timeout=5)

    items = store.items()
    assert len(items) == MAX_HISTORY  # exactly the cap survives
    assert all(it in valid for it in items)  # no torn writes
    assert len(set(items)) == len(items)     # no duplication under contention
