import json
import io
import socket
from http.client import IncompleteRead
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from sweeper.internet_archive import (
    ArchiveQuery,
    ArchiveDiscoveryConfig,
    build_search_url,
    discover_archive,
    owns_identifier,
    parse_search_page,
    partition_bucket,
)


FIXTURES = Path(__file__).parent / "fixtures" / "internet_archive"


def test_partition_is_stable_and_owned_sets_do_not_overlap():
    identifiers = ["pilgrimsprogress00buny", "cityofgod01augu", "sermons01spur"]
    expected = [5, 6, 4]

    assert [partition_bucket(value) for value in identifiers] == expected
    for value in identifiers:
        assert owns_identifier(value, 7, frozenset(range(5))) != owns_identifier(
            value, 7, frozenset({5, 6})
        )


@pytest.mark.parametrize("identifier", ["", "   "])
def test_partition_rejects_an_empty_identifier(identifier):
    with pytest.raises(ValueError, match="identifier"):
        partition_bucket(identifier)


def test_partition_rejects_invalid_ownership_configuration():
    with pytest.raises(ValueError, match="modulus"):
        owns_identifier("book", 1, frozenset({0}))
    with pytest.raises(ValueError, match="buckets"):
        owns_identifier("book", 7, frozenset())
    with pytest.raises(ValueError, match="bucket"):
        owns_identifier("book", 7, frozenset({7}))


def test_search_url_preserves_identity_only_contract_and_cursor():
    query = ArchiveQuery(
        query="mediatype:texts AND language:eng",
        fields=("identifier", "title", "creator"),
        sort=("identifier asc",),
        rows=500,
    )

    parsed = urlparse(build_search_url(query, cursor="W3siaWRlbnRpZmllciI6ImFiYyJ9XQ=="))
    values = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "archive.org"
    assert parsed.path == "/advancedsearch.php"
    assert values["q"] == ["mediatype:texts AND language:eng"]
    assert values["fl[]"] == ["identifier", "title", "creator"]
    assert values["sort[]"] == ["identifier asc"]
    assert values["rows"] == ["500"]
    assert values["cursorMark"] == ["W3siaWRlbnRpZmllciI6ImFiYyJ9XQ=="]
    assert values["output"] == ["json"]


def test_search_query_rejects_unsafe_page_size_and_unstable_sort():
    with pytest.raises(ValueError, match="rows"):
        ArchiveQuery("mediatype:texts", ("identifier",), ("identifier asc",), 501)
    with pytest.raises(ValueError, match="identifier"):
        ArchiveQuery("mediatype:texts", ("identifier",), ("downloads desc",), 500)


def test_parse_search_page_returns_immutable_identity_records():
    payload = (FIXTURES / "search_page_1.json").read_bytes()

    page = parse_search_page(payload)

    assert page.num_found == 3
    assert page.next_cursor == "next-page-token"
    assert [record.identifier for record in page.records] == [
        "pilgrimsprogress00buny",
        "cityofgod01augu",
        "sermons01spur",
    ]
    assert page.records[0].title == "The Pilgrim's Progress"
    assert page.records[0].creators == ("John Bunyan",)


@pytest.mark.parametrize(
    "payload, message",
    [
        (b"not json", "JSON"),
        (json.dumps({"response": {}}).encode(), "docs"),
        (json.dumps({"response": {"docs": [{}], "numFound": 1}}).encode(), "identifier"),
    ],
)
def test_parse_search_page_fails_closed_on_malformed_payload(payload, message):
    with pytest.raises(ValueError, match=message):
        parse_search_page(payload)


def _page(documents, next_cursor):
    return json.dumps({
        "response": {"numFound": 4, "docs": documents},
        "nextCursorMark": next_cursor,
    }).encode()


def test_discovery_counts_candidate_ceiling_after_partition(tmp_path):
    responses = iter([
        _page([
            {"identifier": "book0", "title": "unowned"},
            {"identifier": "book1", "title": "owned one"},
        ], "cursor-2"),
        _page([
            {"identifier": "book4", "title": "unowned"},
            {"identifier": "book2", "title": "owned two"},
            {"identifier": "book3", "title": "must not be consumed"},
        ], "cursor-3"),
    ])
    calls = []

    def opener(request, timeout=0):
        calls.append(request.full_url)
        return io.BytesIO(next(responses))

    config = ArchiveDiscoveryConfig(
        query=ArchiveQuery("mediatype:texts", ("identifier", "title"),
                           ("identifier asc",), 500),
        modulus=7,
        buckets=frozenset(range(5)),
        max_candidates=2,
        requests_per_second=10,
        checkpoint_root=tmp_path,
    )

    report = discover_archive(config, opener=opener, sleeper=lambda _: None)

    assert report.screened_source == 4
    assert report.screened_owned == 2
    assert report.completed_reason == "candidate-ceiling"
    assert len(calls) == 2
    records = [json.loads(line) for line in
               (tmp_path / "checkpoint-000001.jsonl").read_text().splitlines()]
    records += [json.loads(line) for line in
                (tmp_path / "checkpoint-000002.jsonl").read_text().splitlines()]
    assert [record["identifier"] for record in records] == ["book1", "book2"]


def test_discovery_resume_verifies_receipts_and_does_not_rescan(tmp_path):
    response = _page([{"identifier": "book1", "title": "owned"}], None)
    config = ArchiveDiscoveryConfig(
        query=ArchiveQuery("mediatype:texts", ("identifier", "title"),
                           ("identifier asc",), 500),
        modulus=7,
        buckets=frozenset(range(5)),
        max_candidates=1,
        requests_per_second=10,
        checkpoint_root=tmp_path,
    )
    discover_archive(config, opener=lambda request, timeout=0: io.BytesIO(response),
                     sleeper=lambda _: None)

    def forbidden_opener(request, timeout=0):
        raise AssertionError("completed discovery must not request the source again")

    resumed = discover_archive(config, opener=forbidden_opener, sleeper=lambda _: None)
    assert resumed.screened_owned == 1
    assert resumed.completed_reason == "candidate-ceiling"

    receipt = tmp_path / "checkpoint-000001.receipt.json"
    receipt.write_text(receipt.read_text().replace("a", "b", 1))
    with pytest.raises(ValueError, match="receipt"):
        discover_archive(config, opener=forbidden_opener, sleeper=lambda _: None)


def test_discovery_retries_explicit_transient_failures_then_succeeds(tmp_path):
    outcomes = iter([
        socket.timeout("slow"),
        ConnectionResetError("reset"),
        IncompleteRead(b"partial", 10),
        io.BytesIO(_page([{"identifier": "book1", "title": "owned"}], None)),
    ])
    sleeps = []

    def opener(request, timeout=0):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    config = ArchiveDiscoveryConfig(
        query=ArchiveQuery("mediatype:texts", ("identifier", "title"),
                           ("identifier asc",), 500),
        modulus=7,
        buckets=frozenset(range(5)),
        max_candidates=1,
        requests_per_second=2,
        checkpoint_root=tmp_path,
    )

    report = discover_archive(config, opener=opener, sleeper=sleeps.append)

    assert report.screened_owned == 1
    assert sleeps == [0.5, 1.0, 2.0]


def test_discovery_exhausted_retry_budget_is_distinct_and_resumable(tmp_path):
    attempts = 0

    def opener(request, timeout=0):
        nonlocal attempts
        attempts += 1
        raise socket.timeout("still slow")

    config = ArchiveDiscoveryConfig(
        query=ArchiveQuery("mediatype:texts", ("identifier",),
                           ("identifier asc",), 500),
        modulus=7,
        buckets=frozenset(range(5)),
        max_candidates=1,
        requests_per_second=2,
        checkpoint_root=tmp_path,
    )

    report = discover_archive(config, opener=opener, sleeper=lambda _: None)

    assert attempts == 4
    assert report.completed_reason == "transient-deferred"
    assert report.screened_source == 0
    assert not (tmp_path / "checkpoint-000001.jsonl").exists()


def test_discovery_does_not_retry_schema_errors(tmp_path):
    attempts = 0

    def opener(request, timeout=0):
        nonlocal attempts
        attempts += 1
        return io.BytesIO(b'{"response":{}}')

    config = ArchiveDiscoveryConfig(
        query=ArchiveQuery("mediatype:texts", ("identifier",),
                           ("identifier asc",), 500),
        modulus=7,
        buckets=frozenset(range(5)),
        max_candidates=1,
        requests_per_second=2,
        checkpoint_root=tmp_path,
    )

    with pytest.raises(ValueError, match="docs"):
        discover_archive(config, opener=opener, sleeper=lambda _: None)
    assert attempts == 1


def test_discovery_reports_durable_progress_after_each_checkpoint(tmp_path):
    responses = iter([
        _page([{"identifier": "book1", "title": "one"}], "cursor-2"),
        _page([{"identifier": "book2", "title": "two"}], None),
    ])
    observed = []
    config = ArchiveDiscoveryConfig(
        query=ArchiveQuery("mediatype:texts", ("identifier", "title"),
                           ("identifier asc",), 500),
        modulus=7,
        buckets=frozenset(range(5)),
        max_candidates=10,
        requests_per_second=10,
        checkpoint_root=tmp_path,
    )

    discover_archive(
        config,
        opener=lambda request, timeout=0: io.BytesIO(next(responses)),
        sleeper=lambda _: None,
        progress=observed.append,
    )

    assert [(row.screened_owned, row.checkpoints, row.completed_reason)
            for row in observed] == [
        (1, 1, ""),
        (2, 2, "source-exhausted"),
    ]
    assert all((tmp_path / f"checkpoint-{number:06d}.receipt.json").exists()
               for number in (1, 2))
