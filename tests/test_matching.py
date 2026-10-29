from dataclasses import replace

import pytest

from atsfill.core import attempts
from atsfill.core.conditions import Form, faults
from atsfill.core.matching import check, knockouts, matches, problems
from atsfill.core.results import AllOf, AnyOf, Field, FieldType, Is, OptionSet, Status

LEVELS = ["Native-level fluency", "Full professional fluency"]


def field(kind: FieldType, options: list[str] | None = None, required: bool = True) -> Field:
    return Field("f", "F", kind, required, options)


@pytest.mark.parametrize(
    "kind, options, answer, read_back, match",
    [
        (FieldType.TEXT, None, " Île ", "Île", True),
        (FieldType.TEXT, None, "I\u0302le", "Île", True),
        (FieldType.TEXT, None, "a", "b", False),
        (FieldType.DATE, None, "2026-11-02", "2026-11-02", True),
        (FieldType.DATE, None, "2026-11-02", "2026-02-11", False),
        (FieldType.CHOICE, LEVELS, LEVELS[1], LEVELS[1], True),
        (FieldType.CHOICE, LEVELS, LEVELS[1], LEVELS[0], False),
        (FieldType.CHOICE, LEVELS, "Fluent", "Fluent", False),
        (FieldType.MULTI_CHOICE, LEVELS, LEVELS, LEVELS[::-1], True),
        (FieldType.MULTI_CHOICE, LEVELS, LEVELS, LEVELS[:1], False),
        (FieldType.TYPEAHEAD, None, "Paris", "Paris, Île-de-France, France", True),
        (FieldType.TYPEAHEAD, None, "Paris", "Lyon, France", False),
        (FieldType.TYPEAHEAD, None, "Paris, France", "Paris, Île-de-France, France", True),
        (FieldType.TYPEAHEAD, None, "Paris, France", "Paris, Texas, United States", False),
        (FieldType.TYPEAHEAD, None, "Paris, France", "Paris, Île-de-France, FRA", True),
        (FieldType.TYPEAHEAD, None, "Paris, FRA", "Paris, Île-de-France, France", True),
        (FieldType.TYPEAHEAD, None, "Paris, France", "Paris, Texas, USA", False),
        (FieldType.FILE, None, "/a/resume.pdf", "resume.pdf", True),
        (FieldType.FILE, None, "/a/resume.pdf", "cv.pdf", False),
        (FieldType.PHONE, None, "+33 6 12 34 56 78", "+33612345678", True),
        (FieldType.PHONE, None, "+33 6 12 34 56 78", "+44612345678", False),
        (FieldType.URL, None, "https://example.com ", "https://example.com", True),
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
    prefilled = Field("f", "F", FieldType.TEXT, True, prefilled="Ada")
    assert problems([prefilled], {}) == []  # left to the account's value
    default = Field("f", "F", FieldType.TEXT, True, default="France")
    assert problems([default], {}) == ["no answer for required f (F)"]  # the page's, not theirs
    assert problems(fields, {"f": ["Fluent"], "cv": "/no/resume.pdf"}) == [
        "f: a choice answer is a string",
        "cv: no file at /no/resume.pdf",
    ]
    langs = Field("l", "Languages", FieldType.MULTI_CHOICE, False, LEVELS)
    assert problems([langs], {"l": LEVELS[0]}) == ["l: a multi_choice answer is a list of strings"]
    date = Field("d", "Start", FieldType.DATE, False)
    assert problems([date], {"d": "11/02/2026"}) == ["d: a date answer is YYYY-MM-DD"]
    assert problems([date], {"d": "2026-11-02"}) == []
    name = Field("n", "Name", FieldType.TEXT, True, max_length=5)
    assert problems([name], {"n": "Jane Doe"}) == ["n: 8 characters, over the limit of 5"]
    assert problems([name], {"n": " Jane "}) == []  # trimmed, as the read-back is
    note = Field("m", "Message", FieldType.TEXT, True, min_length=5)
    assert problems([note], {"m": "Hi"}) == ["m: 2 characters, under the minimum of 5"]
    site = Field("s", "LinkedIn", FieldType.URL, False)
    assert problems([site], {"s": "janedoe"}) == ["s: a url answer is an absolute http(s) URL"]
    assert problems([site], {"s": "linkedin.com/in/janedoe"}) == [
        "s: a url answer is an absolute http(s) URL"
    ]
    assert problems([site], {"s": " https://www.linkedin.com/in/janedoe "}) == []


def test_a_click_without_outcome_blocks_the_next_send(tmp_path):
    attempts.record(tmp_path, "p", attempts.PENDING)
    assert attempts.blocks_send(tmp_path, "p")
    attempts.record(tmp_path, "p", Status.BLOCKED)
    assert not attempts.blocks_send(tmp_path, "p")
    attempts.record(tmp_path, "p", Status.SUBMITTED)
    assert attempts.blocks_send(tmp_path, "p")
    assert not attempts.blocks_send(tmp_path, "other")


VISA = Field("visa", "Need a visa?", FieldType.CHOICE, True, ["Yes", "No"])
WHICH = Field("which", "Which visa?", FieldType.TEXT, True, shown_when=Is("visa", ["Yes"]))


def test_a_required_follow_up_is_required_once_its_condition_holds():
    assert problems([VISA, WHICH], {"visa": "No"}) == []
    assert problems([VISA, WHICH], {"visa": "Yes"}) == [
        "no answer for required which (Which visa?)"
    ]


def test_an_answer_to_a_follow_up_kept_hidden_refuses():
    assert problems([VISA, WHICH], {"visa": "No", "which": "H-1B"}) == [
        'which: not asked, it shows when "Need a visa?" is "Yes"'
    ]


def test_a_follow_up_of_a_hidden_follow_up_stays_hidden():
    since = Field("since", "Since?", FieldType.TEXT, True, shown_when=Is("which"))
    assert problems([VISA, WHICH, since], {"visa": "No"}) == []
    assert problems([VISA, WHICH, since], {"visa": "Yes", "which": "H-1B"}) == [
        "no answer for required since (Since?)"
    ]


@pytest.mark.parametrize(
    "condition, answers, shown",
    [
        (Is("visa", ["Yes"], negated=True), {"visa": "No"}, True),
        (Is("visa", ["Yes"], negated=True), {}, False),  # against values, never unanswered
        (Is("visa", negated=True), {}, True),  # unanswered
        (Is("langs", ["Fr"]), {"langs": ["En", "Fr"]}, True),
        (AnyOf([Is("visa", ["Yes"]), Is("langs")]), {"langs": ["En"]}, True),
        (AllOf([Is("visa", ["Yes"]), Is("langs")]), {"langs": ["En"]}, False),
    ],
)
def test_conditions(condition, answers, shown):
    langs = Field("langs", "Languages", FieldType.MULTI_CHOICE, False, ["En", "Fr"])
    follow_up = Field("f", "F", FieldType.TEXT, False, shown_when=condition)
    assert Form([VISA, langs, follow_up], answers).shown(follow_up) is shown


def test_a_condition_reads_the_prefilled_value_then_the_default():
    which = Field("which", "Which visa?", FieldType.TEXT, True, shown_when=Is("visa", ["Yes"]))
    for visa in (replace(VISA, prefilled="Yes"), replace(VISA, default="Yes")):
        assert Form([visa, which], {}).shown(which)
        assert not Form([visa, which], {"visa": "No"}).shown(which)  # the answer first


def test_required_when_an_answer_asks_it():
    name = Field("n", "Name", FieldType.TEXT, False, required_when=Is("visa", ["Yes"]))
    assert problems([VISA, name], {"visa": "No"}) == []
    assert problems([VISA, name], {"visa": "Yes"}) == ["no answer for required n (Name)"]


def test_a_hidden_follow_up_that_reads_back_a_value_is_a_mismatch():
    checks = check([VISA, WHICH], {"visa": "No"}, {"visa": "No", "which": "H-1B"})
    assert [(c.field_id, c.match) for c in checks] == [("visa", True), ("which", False)]


def test_conditions_name_listed_fields_without_a_loop():
    loop = Field("a", "A", FieldType.TEXT, False, shown_when=Is("b"))
    back = Field("b", "B", FieldType.TEXT, False, shown_when=Is("a"))
    stray = Field("c", "C", FieldType.TEXT, False, shown_when=Is("x"))
    assert faults([loop, back, stray]) == [
        "c: its condition names no field x",
        "a: shown by its own answer",
        "b: shown by its own answer",
    ]


CV = Field("cv", "Resume", FieldType.FILE, True, one_of="resume")
CV_TEXT = Field("cv_text", "Resume text", FieldType.TEXT, True, one_of="resume")


def test_a_required_group_takes_one_alternative(tmp_path):
    path = tmp_path / "cv.pdf"
    path.write_text("")
    assert problems([CV, CV_TEXT], {"cv_text": "Jane Doe, engineer"}) == []
    assert problems([CV, CV_TEXT], {"cv": str(path)}) == []
    assert problems([replace(CV, prefilled="cv.pdf"), CV_TEXT], {}) == []
    assert problems([CV, CV_TEXT], {}) == ["no answer for required resume (Resume or Resume text)"]
    assert problems([CV, CV_TEXT], {"cv": str(path), "cv_text": "Jane Doe"}) == [
        "resume: one of cv, cv_text, not both"
    ]


def test_an_optional_group_takes_none():
    optional = [replace(CV, required=False), replace(CV_TEXT, required=False)]
    assert problems(optional, {}) == []


def test_a_group_has_alternatives_agreeing_on_required():
    assert faults([CV, CV_TEXT]) == []
    assert faults([CV]) == ["resume: one alternative, cv"]
    assert faults([CV, replace(CV_TEXT, required=False)]) == [
        "resume: alternatives disagree on required"
    ]


COUNTRY = Field("country", "Country", FieldType.CHOICE, True, ["US", "CA", "FR"])
STATE = Field(
    "state",
    "State",
    FieldType.CHOICE,
    True,
    ["Elsewhere"],
    option_sets=[
        OptionSet(Is("country", ["US"]), ["Texas", "Ohio"]),
        OptionSet(Is("country", ["CA"]), ["Ontario"]),
        OptionSet(Is("country", ["FR"]), []),
    ],
)


def test_a_dependent_list_takes_the_options_its_parent_leaves():
    assert problems([COUNTRY, STATE], {"country": "CA", "state": "Ontario"}) == []
    assert problems([COUNTRY, STATE], {"country": "CA", "state": "Texas"}) == [
        "state: 'Texas' is not one of ['Ontario'], the options when \"Country\" is \"CA\""
    ]
    optional = replace(COUNTRY, required=False)
    for country in (replace(optional, prefilled="US"), replace(optional, default="US")):
        assert problems([country, STATE], {"state": "Texas"}) == []
        assert problems([country, STATE], {"country": "CA", "state": "Texas"})  # the answer first


def test_a_dependent_list_falls_back_to_its_options():
    other = replace(COUNTRY, options=["US", "CA", "FR", "DE"])
    assert problems([other, STATE], {"country": "DE", "state": "Elsewhere"}) == []
    assert problems([other, STATE], {"country": "DE", "state": "Bavaria"}) == [
        "state: 'Bavaria' is not one of ['Elsewhere']"
    ]
    unknown = replace(STATE, options=None)  # left to the read-back
    assert problems([other, unknown], {"country": "DE", "state": "Bavaria"}) == []
    checks = check([other, unknown], {"country": "DE", "state": "Bavaria"}, {"state": "Bayern"})
    assert [c.match for c in checks] == [False, False]


def test_an_empty_option_set_refuses_any_answer():
    assert problems([COUNTRY, STATE], {"country": "FR", "state": "Paris"}) == [
        'state: \'Paris\' is not one of [], the options when "Country" is "FR"'
    ]


def test_an_option_set_under_two_parents():
    remote = Field("remote", "Remote?", FieldType.CHOICE, True, ["Yes", "No"])
    office = Field(
        "office",
        "Office",
        FieldType.CHOICE,
        True,
        option_sets=[OptionSet(AllOf([Is("country", ["US"]), Is("remote", ["No"])]), ["Austin"])],
    )
    fields = [COUNTRY, remote, office]
    assert problems(fields, {"country": "US", "remote": "No", "office": "Paris"}) == [
        "office: 'Paris' is not one of ['Austin'], the options when "
        '("Country" is "US") and ("Remote?" is "No")'
    ]
    assert problems(fields, {"country": "US", "remote": "Yes", "office": "Paris"}) == []


def test_option_sets_name_listed_fields_and_not_their_own():
    stray = replace(STATE, option_sets=[OptionSet(Is("x"), [])])
    own = replace(STATE, id="own", option_sets=[OptionSet(Is("own"), [])])
    assert faults([stray, own]) == [
        "state: its condition names no field x",
        "own: its options follow its own answer",
    ]


def test_a_knockout_answer_is_warned_never_refused():
    fields = [
        Field(
            "f", "Still employed here?", FieldType.CHOICE, True, ["Yes", "No"], knockouts=["Yes"]
        ),
        Field("m", "Shifts", FieldType.MULTI_CHOICE, False, ["Day", "Night"], knockouts=["Night"]),
    ]
    answers = {"f": "Yes", "m": ["Day", "Night"]}
    assert problems(fields, answers) == []
    assert knockouts(fields, answers) == [
        "f: 'Yes' ends the application (Still employed here?)",
        "m: 'Night' ends the application (Shifts)",
    ]
    assert knockouts(fields, {"f": "No", "m": ["Day"]}) == []
