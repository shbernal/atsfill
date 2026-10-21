from scripts import gate


def test_skipped_postings_are_listed_apart_from_not_ready():
    rows = [
        {"status": "ready"},
        {"status": "unsupported", "label": "unhandled", "reason": "unhandled_rendering: q is a x"},
        {"status": "unsupported", "label": "unclear", "reason": "posting_gone"},
        {"status": "unsupported", "label": "out_of_scope", "reason": "out_of_scope: v is a video"},
        {"status": "unsupported", "label": "unclear", "reason": "posting_gone"},
    ]
    assert gate.stops(rows) == [
        "not ready (1): unsupported, unhandled, unhandled_rendering: q is a x",
        "skipped (2): posting_gone",
        "skipped (1): out_of_scope: v is a video",
    ]
