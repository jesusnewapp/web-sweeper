import io
import json
from pathlib import Path

import pytest

from sweeper.archive_lane import DiscoveryLaneConfig, run_discovery_lane


def _page(documents, next_cursor=None):
    return json.dumps({
        "response": {"numFound": len(documents), "docs": documents},
        "nextCursorMark": next_cursor,
    }).encode()


def test_discovery_lane_holds_one_owner_and_publishes_durable_ui_progress(tmp_path):
    root = tmp_path / "lane"
    config = DiscoveryLaneConfig(
        campaign_id="internet-archive-christian-general-h0-4-20260831",
        root=root,
        query="mediatype:texts AND language:eng",
        max_accepted=20_000,
        max_candidates=1,
        modulus=7,
        buckets=frozenset(range(5)),
        rows=500,
        requests_per_second=1,
    )

    def opener(request, timeout=0):
        return io.BytesIO(_page([{"identifier": "book1", "title": "Bible study"}]))

    report = run_discovery_lane(config, opener=opener, sleeper=lambda _: None)

    state = json.loads((root / "state.json").read_text())
    assert report.screened_owned == 1
    assert state["stage"] == "bounded-frontier-complete"
    assert state["candidateCount"] == 1
    assert state["candidateTarget"] == 1
    assert state["accepted"] == 0
    assert state["target"] == 20_000
    assert state["publicationAuthorized"] is False
    assert (root / "journal" / "activity.jsonl").exists()


def test_discovery_lane_rejects_publication_authority(tmp_path):
    with pytest.raises(ValueError, match="publication"):
        DiscoveryLaneConfig(
            campaign_id="lane",
            root=tmp_path,
            query="mediatype:texts",
            max_accepted=20_000,
            max_candidates=1,
            modulus=7,
            buckets=frozenset(range(5)),
            publication_authorized=True,
        )
