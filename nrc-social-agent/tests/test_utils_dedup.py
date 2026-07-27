from src.utils.dedup import SeenUpdateTracker


def test_seen_before_returns_false_the_first_time():
    tracker = SeenUpdateTracker()

    assert tracker.seen_before(1) is False


def test_seen_before_returns_true_on_repeat():
    tracker = SeenUpdateTracker()

    tracker.seen_before(1)

    assert tracker.seen_before(1) is True


def test_seen_before_tracks_distinct_ids_independently():
    tracker = SeenUpdateTracker()

    tracker.seen_before(1)

    assert tracker.seen_before(2) is False


def test_tracker_evicts_oldest_entry_once_max_tracked_is_exceeded():
    tracker = SeenUpdateTracker(max_tracked=2)

    tracker.seen_before(1)
    tracker.seen_before(2)
    tracker.seen_before(3)  # evicts 1

    assert tracker.seen_before(1) is False  # forgotten, treated as new again
    assert tracker.seen_before(3) is True  # still remembered
