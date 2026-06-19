import threading

from flow.history import MAX_HISTORY, History


def test_add_then_items_returns_newest_first():
    h = History()
    h.add("first")
    h.add("second")
    h.add("third")
    assert h.items() == ["third", "second", "first"]


def test_caps_at_max_history_and_evicts_oldest():
    h = History()
    total = MAX_HISTORY + 5
    for i in range(total):
        h.add(str(i))
    items = h.items()
    assert len(items) == MAX_HISTORY
    # Newest first; the oldest 5 were evicted in order.
    assert items == [str(i) for i in range(total - 1, total - 1 - MAX_HISTORY, -1)]


def test_clear_empties_history():
    h = History()
    h.add("a")
    h.add("b")
    h.clear()
    assert h.items() == []


def test_concurrent_adds_are_threadsafe():
    """Many threads adding at once must never lose or garble an entry."""
    h = History()
    threads_n = 8
    per_thread = 50
    start = threading.Barrier(threads_n)
    valid = {f"{t}-{n}" for t in range(threads_n) for n in range(per_thread)}

    def worker(tid):
        start.wait()  # release all threads together for maximum contention
        for n in range(per_thread):
            h.add(f"{tid}-{n}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
    for thr in threads:
        thr.start()
    for thr in threads:
        thr.join(timeout=5)

    items = h.items()
    assert len(items) == MAX_HISTORY  # deque holds exactly the cap
    # Every survivor is an intact value that was actually added (no torn
    # writes), and all are distinct (no duplication under contention).
    assert all(it in valid for it in items)
    assert len(set(items)) == len(items)
