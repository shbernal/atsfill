from atsfill.core import attempts
from atsfill.core.matching import matches, problems
from atsfill.core.results import Field, FieldType, Status

LEVELS = ["Native-level fluency", "Full professional fluency"]


def field(kind: FieldType, options: list[str] | None = None, required: bool = True) -> Field:
    return Field("f", "F", kind, required, options)


def test_text_ignores_surrounding_whitespace_and_normalization():
    assert matches(field(FieldType.TEXT), " Île ", "Île")
    assert not matches(field(FieldType.TEXT), "a", "b")


def test_choice_must_be_an_option():
    assert matches(field(FieldType.CHOICE, LEVELS), LEVELS[1], LEVELS[1])
    assert not matches(field(FieldType.CHOICE, LEVELS), "Fluent", "Fluent")


def test_typeahead_matches_the_picked_suggestion():
    assert matches(field(FieldType.TYPEAHEAD), "Paris", "Paris, France")
    assert not matches(field(FieldType.TYPEAHEAD), "Paris", "Lyon, France")


def test_file_compares_base_names():
    assert matches(field(FieldType.FILE), "/a/resume.pdf", "resume.pdf")


def test_nothing_read_back_is_a_mismatch():
    assert not matches(field(FieldType.TEXT), "a", None)


def test_problems_before_filling():
    fields = [field(FieldType.CHOICE, LEVELS), Field("cv", "CV", FieldType.FILE, False)]
    assert problems(fields, {"f": "Fluent", "cv": "resume.pdf", "x": "?"}) == [
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
