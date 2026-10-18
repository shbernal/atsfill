"""Ashby (`jobs.ashbyhq.com`).

Every change posts `ApiSetFormValue`, whose response holds the whole form as Ashby will
receive it: that is the read-back. The location combobox saves only a picked suggestion.
Radio inputs are hidden behind their option's text. Submit runs reCAPTCHA Enterprise; a low
score comes back as `RECAPTCHA_SCORE_BELOW_THRESHOLD`, shown as "flagged as possible spam".
"""

import json
import time
from typing import Any
from urllib.parse import urlsplit

from atsfill.core.adapter import Blocked, Unsupported
from atsfill.core.browser import Page, Response
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = "jobs.ashbyhq.com"
TYPES = {
    "String": FieldType.TEXT,
    "Email": FieldType.TEXT,
    "LongText": FieldType.TEXT,
    "Date": FieldType.TEXT,
    "Number": FieldType.TEXT,
    "Phone": FieldType.PHONE,
    "File": FieldType.FILE,
    "Location": FieldType.TYPEAHEAD,
    "ValueSelect": FieldType.CHOICE,
    "Boolean": FieldType.CHOICE,
    "MultiValueSelect": FieldType.MULTI_CHOICE,
}
SETTLE_S = 1.5
READ_BACK_TIMEOUT_S = 30
SUBMIT_TIMEOUT_MS = 90_000


def _op(response: Response) -> str:
    return response.url.partition("?op=")[2] if "/api/non-user-graphql" in response.url else ""


class Ashby:
    name = "ashby"
    side_effects = [
        SideEffect(
            "fill", "hiring_system", "each answer and the resume are saved to Ashby as typed"
        ),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._render: dict[str, Any] | None = None
        self._pending = 0
        self._last_change = 0.0

    def detect(self, url: str, page: Page | None) -> bool:
        return urlsplit(url).hostname == HOST

    def posting(self, url: str) -> str:
        org, job = urlsplit(url).path.strip("/").split("/")[:2]
        return f"https://{HOST}/{org}/{job}"

    def read_api(self, url: str) -> list[Field] | None:
        return None

    def read_form(self, page: Page) -> list[Field]:
        page.on("request", self._on_request)
        page.on("requestfinished", self._on_finished)
        page.on("requestfailed", self._on_finished)
        page.on("response", self._on_response)
        with page.expect_response(lambda r: _op(r) == "ApiJobPosting") as posting:
            page.goto(self.posting(page.url) + "/application")
        job = posting.value.json()["data"]["jobPosting"]
        if job is None:
            raise Unsupported("posting_gone")
        if job.get("surveyForms"):
            raise Unsupported("form_shape: survey forms after the application form")
        self._render = job["applicationForm"]
        page.wait_for_selector(".ashby-application-form-submit-button")
        return [self._field(e) for e in self._entries()]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        fields = {e["field"]["path"]: self._field(e) for e in self._entries()}
        for field_id, answer in answers.items():
            entry = page.locator(f'[data-field-path="{field_id}"]')
            entry.scroll_into_view_if_needed()
            match fields[field_id].type, answer:
                case FieldType.TEXT | FieldType.PHONE, str():
                    box = entry.locator("input, textarea").first
                    box.fill(answer)
                    box.blur()
                case FieldType.CHOICE, str():
                    entry.get_by_text(answer, exact=True).click()
                case FieldType.MULTI_CHOICE, list():
                    for value in answer:
                        entry.get_by_text(value, exact=True).click()
                case FieldType.TYPEAHEAD, str():
                    box = entry.locator("input[role=combobox]")
                    box.fill(answer)
                    page.get_by_role("option").filter(has_text=answer).first.click()
                case FieldType.FILE, str():
                    entry.locator("input[type=file]").set_input_files(answer)
                case kind, _:
                    raise Unsupported(
                        f"form_shape: cannot fill {field_id}, a {kind}, with {answer!r}"
                    )
        self._answered = list(answers)

    def read_back(self, page: Page) -> dict[str, Answer]:
        """Ashby's last returned form, once every answer is held and no request is in flight."""
        deadline = time.monotonic() + READ_BACK_TIMEOUT_S
        while time.monotonic() < deadline:
            values = self._values()
            quiet = self._pending == 0 and time.monotonic() - self._last_change > SETTLE_S
            if quiet and all(values.get(i) is not None for i in self._answered):
                break
            page.wait_for_timeout(250)
        return {k: v for k, v in self._values().items() if v is not None}

    def submit(self, page: Page) -> str | None:
        with page.expect_response(
            lambda r: _op(r).startswith("ApiSubmit") and _op(r).endswith("FormAction"),
            timeout=SUBMIT_TIMEOUT_MS,
        ) as submitted:
            page.locator(".ashby-application-form-submit-button").click()
        response = submitted.value
        text = response.text()
        if "RECAPTCHA_SCORE_BELOW_THRESHOLD" in text or "spam" in text.lower():
            raise Blocked("captcha_score")
        body = json.loads(text)
        if body.get("errors"):
            raise RuntimeError(f"{_op(response)} errors: {body['errors']}")
        action = next(iter(body["data"].values()))
        if block := (action.get("messages") or {}).get("blockMessageForCandidateHtml"):
            raise Blocked(f"blocked_by_system: {block}")
        outcome = action["applicationFormResult"]
        if outcome["__typename"] == "FormSubmitSuccess":
            return f"{_op(response)} → FormSubmitSuccess ({response.status}, {page.url})"
        errors = outcome.get("errorMessages") or outcome.get("formErrors")
        raise Blocked(f"form_errors: {errors}")

    def _entries(self) -> list[dict[str, Any]]:
        assert self._render is not None
        return [
            e
            for s in self._render["sections"]
            if not s.get("isHidden")
            for e in s["fieldEntries"]
            if not e.get("isHidden")
        ]

    @staticmethod
    def _field(entry: dict[str, Any]) -> Field:
        f = entry["field"]
        kind = TYPES.get(f["type"])
        if kind is None:
            raise Unsupported(f"form_shape: {f['path']} is an Ashby {f['type']} field")
        options = [v["label"] for v in f.get("selectableValues") or []] or None
        if f["type"] == "Boolean":
            options = ["Yes", "No"]
        return Field(f["path"], f["title"], kind, bool(entry["isRequired"]), options)

    def _values(self) -> dict[str, Answer | None]:
        return {e["field"]["path"]: self._value(e) for e in self._entries()}

    @staticmethod
    def _value(entry: dict[str, Any]) -> Answer | None:
        held = entry.get("fieldValue")
        if not held:
            return None
        if held["__typename"] == "File":
            return held["filename"]
        if held["__typename"] == "FileList":
            return [f["filename"] for f in held["files"]]
        value = held.get("value")
        labels = {v["value"]: v["label"] for v in entry["field"].get("selectableValues") or []}
        match value:
            case bool():
                return "Yes" if value else "No"
            case {"text": str(text)}:
                return text
            case list():
                return [labels.get(v, str(v)) for v in value]
            case None:
                return None
            case _:
                return labels.get(value, str(value))

    def _on_request(self, request: Any) -> None:
        if "/api/non-user-graphql" in request.url:
            self._pending += 1

    def _on_finished(self, request: Any) -> None:
        if "/api/non-user-graphql" in request.url:
            self._pending -= 1
            self._last_change = time.monotonic()

    def _on_response(self, response: Response) -> None:
        if _op(response) == "ApiSetFormValue" and response.ok:
            render = response.json()["data"]["setFormValue"]
            if render:
                self._render = render
