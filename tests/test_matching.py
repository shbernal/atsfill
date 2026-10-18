import pytest

from atsfill.core import attempts
from atsfill.core.matching import matches, problems
from atsfill.core.results import Field, FieldType, Status

LEVELS = ["Native-level fluency", "Full professional fluency"]


def field(kind: FieldType, options: list[str] | None = None, required: bool = True) -> Field:
    return Field("f", "F", kind, required, options)


@pytest.mark.parametrize(
    "kind, options, answer, read_back, match",
    [
        (FieldType.TEXT, None, " Île ", "Île", True),
        (FieldType.TEXT, None, "I\u0302le", "Île", True),
        (FieldType.TEXT, None, "a", "b", False),
        (FieldType.CHOICE, LEVELS, LEVELS[1], LEVELS[1], True),
        (FieldType.CHOICE, LEVELS, LEVELS[1], LEVELS[0], False),
        (FieldType.CHOICE, LEVELS, "Fluent", "Fluent", False),
        (FieldType.MULTI_CHOICE, LEVELS, LEVELS, LEVELS[::-1], True),
        (FieldType.MULTI_CHOICE, LEVELS, LEVELS, LEVELS[:1], False),
        (FieldType.TYPEAHEAD, None, "Paris", "Paris, Île-de-France, France", True),
        (FieldType.TYPEAHEAD, None, "Paris", "Lyon, France", False),
        (FieldType.FILE, None, "/a/resume.pdf", "resume.pdf", True),
        (FieldType.FILE, None, "/a/resume.pdf", "cv.pdf", False),
        (FieldType.PHONE, None, "+33 6 12 34 56 78", "+33612345678", True),
        (FieldType.PHONE, None, "+33 6 12 34 56 78", "+44612345678", False),
        (FieldType.TEXT, None, "a", None, False),
    ],
)
def test_read_back_matching(kind, options, answer, read_back, match):
    assert matches(field(kind, options), answer, read_back) is match


def test_problems_before_filling():
    fields = [field(FieldType.CHOICE, LEVELS), Field("cv", "CV", FieldType.FILE, False)]
    assert problems(fields, {"x": "?", "f": "Fluent", "cv": "resume.pdf"}) == [
        "no field x",
        f"f: 'Fluent' is not one of {LEVELS}",
        "cv: a file answer is an absolute path",
    ]
    assert problems(fields, {}) == ["no answer for required f (F)"]


def test_a_click_without_outcome_blocks_the_next_send(tmp_path):
    attempts.record(tmp_path, "p", attempts.PENDING)
    assert attempts.blocks_send(tmp_path, "p")
    attempts.record(tmp_path, "p", Status.BLOCKED)
    assert not attempts.blocks_send(tmp_path, "p")
    attempts.record(tmp_path, "p", Status.SUBMITTED)
    assert attempts.blocks_send(tmp_path, "p")
    assert not attempts.blocks_send(tmp_path, "other")
