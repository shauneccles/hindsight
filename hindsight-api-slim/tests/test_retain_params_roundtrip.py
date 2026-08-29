"""retain_params must round-trip whatever the caller supplied.

reprocess_document rebuilds its retain call entirely from documents.retain_params.
The inclusion list this replaces dropped three fields in turn — `strategy`,
`entities`, `resolve_entities` — each written by api_retain and never captured, so
a reprocess re-extracted under the bank's default strategy with entity resolution
it had been told not to do. Every one was invisible: the reprocess succeeds and
only the resulting facts are wrong.

The rule is now an exclusion list, so a new retain field round-trips unless
somebody deliberately excludes it, and api_retain's content dict is the single
source of truth for what gets replayed.
"""

import re
from pathlib import Path

from hindsight_api.engine.retain import orchestrator as orch

API_HTTP = Path(orch.__file__).resolve().parents[2] / "api" / "http.py"
MEMORY_ENGINE = Path(orch.__file__).resolve().parents[2] / "engine" / "memory_engine.py"


def _params(**item):
    retain_params, _tags = orch._build_retain_params([item])
    return retain_params


def test_captures_the_fields_that_used_to_go_missing():
    p = _params(
        content="x",
        strategy="survey",
        entities=[{"text": "Widget", "type": "CONCEPT"}],
        resolve_entities=False,
    )
    assert p["strategy"] == "survey"
    assert p["entities"] == [{"text": "Widget", "type": "CONCEPT"}]
    assert p["resolve_entities"] is False


def test_excludes_what_a_reprocess_supplies_itself():
    """Replaying these would fight the reprocess: content is the stored
    original_text, and document_id/update_mode/tags are set by the reprocess."""
    p = _params(
        content="x", document_id="d1", update_mode="append", tags=["a"], context="ctx"
    )
    assert set(p) == {"context"}


def test_absent_fields_are_not_invented():
    assert _params(content="x") == {}


def test_event_date_is_normalised_for_json():
    from datetime import datetime

    p = _params(content="x", event_date=datetime(2026, 1, 2, 3, 4, 5))
    assert p["event_date"] == "2026-01-02T03:04:05"


def test_a_new_field_round_trips_without_being_listed():
    """The point of inverting the rule: the failure mode is now a deliberate
    exclusion, not a silent omission."""
    assert _params(content="x", some_future_field="v")["some_future_field"] == "v"


def test_every_field_api_retain_sends_can_round_trip():
    """Pairs the writer against the rule so they cannot drift apart.

    api_retain's content dict is the source of truth for what a retain can carry.
    Anything it sets that is neither excluded nor capturable is a field a reprocess
    would silently lose — which is exactly how this bug arose three times.
    """
    written = set(re.findall(r'content_dict\["(\w+)"\]\s*=', API_HTTP.read_text()))
    assert written, "could not locate api_retain's content dict assignments"

    replayable = written - orch._RETAIN_PARAMS_NOT_REPLAYED
    produced = _params(content="x", **{k: "v" for k in replayable})
    missing = sorted(replayable - set(produced))
    assert not missing, f"api_retain sends fields retain_params cannot carry: {missing}"


def test_api_retain_puts_the_strategy_on_the_content_dict():
    """Where the break actually was. api_retain used `strategy` only as the key it
    grouped items by, so it never reached the dict _build_retain_params reads —
    and capturing it in the orchestrator alone changed nothing."""
    src = API_HTTP.read_text()
    block = src[src.index("# Group items by strategy") :][:2500]
    assert 'content_dict["strategy"] = item.strategy' in block


def test_reprocess_replays_retain_params_wholesale():
    """The reader half. Enumerating fields here is the other way the two sides
    drift; it must take retain_params whole and override only its own three."""
    src = MEMORY_ENGINE.read_text()
    block = src[src.index("Rebuild the content dict from retain_params") :][:1800]
    assert "content_dict.update(" in block, "reprocess still cherry-picks fields"
    for own in ("content", "document_id", "update_mode"):
        assert f'content_dict["{own}"]' in block, f"reprocess must set its own {own}"


def test_reprocess_keeps_the_strategy_on_the_dict_so_it_survives_twice():
    """Excluding `strategy` from the replayed dict survived exactly ONE reprocess.

    reprocess pulls it out as a call argument, so the first re-extraction used the
    right strategy — but _build_retain_params never saw it on the item, retain_params
    came back without it, and a second reprocess fell back to the bank default.
    Verified against a live server: two consecutive reprocesses of a survey marker
    both held at 0 memory units only once the field rode on the dict as well.
    """
    src = MEMORY_ENGINE.read_text()
    block = src[src.index("Rebuild the content dict from retain_params") :][:1800]
    assert "content_dict.update(retain_params)" in block, (
        "the replayed dict must carry strategy too, or retain_params loses it on "
        "the first reprocess"
    )
