from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional


ZERO_HASH = "0" * 64
MEANINGFUL_PROOFS = {
    "cursor-advanced", "item-disposition", "artifact-created", "gate-completed",
    "membership-frozen", "validated", "staged", "published", "live-verified",
    "cleanup-verified",
}


class IdempotencyConflict(RuntimeError):
    pass


class LeaseBusy(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


class ProgressRejected(RuntimeError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  command_key TEXT NOT NULL UNIQUE,
  command_hash TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL,
  actor_id TEXT NOT NULL,
  lane_id TEXT,
  aggregate_kind TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;
CREATE TABLE IF NOT EXISTS work_items (
  work_id TEXT PRIMARY KEY,
  lane_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL,
  version INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  last_event_seq INTEGER NOT NULL,
  last_progress_seq INTEGER NOT NULL,
  last_progress_ms INTEGER NOT NULL,
  progress_fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS work_queue ON work_items(lane_id,status,priority,work_id);
CREATE TABLE IF NOT EXISTS leases (
  resource TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  fence INTEGER NOT NULL,
  expires_at_ms INTEGER NOT NULL,
  binding_hash TEXT NOT NULL,
  binding_json TEXT NOT NULL,
  phase TEXT NOT NULL,
  last_event_seq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pivots (
  pivot_id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  lane_id TEXT NOT NULL,
  baseline_seq INTEGER NOT NULL,
  baseline_fingerprint TEXT NOT NULL,
  requested_at_ms INTEGER NOT NULL,
  action_family TEXT,
  checkpoint_sha256 TEXT,
  acknowledged_at_ms INTEGER,
  succeeded_at_ms INTEGER,
  success_seq INTEGER,
  escalated_at_ms INTEGER,
  attempt INTEGER NOT NULL,
  status TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_pivot_per_work
ON pivots(work_id) WHERE status IN ('requested','acknowledged');
"""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class UltraRuntime:
    """Small event/lease kernel shared by Sweeper V2 and Sweeper Ultra."""

    def __init__(self, path: Path, clock_ms=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.db = sqlite3.connect(str(path), timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        lease_columns = {
            str(row[1]) for row in self.db.execute("PRAGMA table_info(leases)")
        }
        if "binding_json" not in lease_columns:
            # Existing runtimes did not retain enough evidence to prove an
            # exact writer binding.  Keep the row fail-closed until a fresh,
            # explicitly keyed acquisition replaces the empty binding.
            self.db.execute(
                "ALTER TABLE leases ADD COLUMN binding_json TEXT NOT NULL DEFAULT '{}'"
            )

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        cursor = self.db.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            yield cursor
        except Exception:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def _existing(self, cursor: sqlite3.Cursor, command_key: str,
                  command_hash: str) -> Optional[sqlite3.Row]:
        row = cursor.execute("SELECT * FROM events WHERE command_key=?",
                             (command_key,)).fetchone()
        if row and row["command_hash"] != command_hash:
            raise IdempotencyConflict("command key was already used with different input")
        return row

    def _append(self, cursor: sqlite3.Cursor, *, command_key: str, actor_id: str,
                lane_id: Optional[str], aggregate_kind: str, aggregate_id: str,
                event_type: str, payload: Dict[str, Any], occurred_at_ms: Optional[int] = None) -> sqlite3.Row:
        command = {"actor": actor_id, "lane": lane_id, "aggregateKind": aggregate_kind,
                   "aggregateId": aggregate_id, "eventType": event_type, "payload": payload}
        command_hash = digest(command)
        existing = self._existing(cursor, command_key, command_hash)
        if existing:
            return existing
        tail = cursor.execute("SELECT seq,event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        sequence = int(tail["seq"]) + 1 if tail else 1
        previous_hash = str(tail["event_hash"]) if tail else ZERO_HASH
        stamp = self.clock_ms() if occurred_at_ms is None else occurred_at_ms
        envelope = {"seq": sequence, "commandHash": command_hash, "occurredAtMs": stamp,
                    "actor": actor_id, "lane": lane_id, "aggregateKind": aggregate_kind,
                    "aggregateId": aggregate_id, "eventType": event_type,
                    "payload": payload, "previousHash": previous_hash}
        event_hash = digest(envelope)
        cursor.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sequence, str(uuid.uuid4()), command_key, command_hash, stamp, actor_id,
             lane_id, aggregate_kind, aggregate_id, event_type, canonical(payload),
             previous_hash, event_hash))
        return cursor.execute("SELECT * FROM events WHERE seq=?", (sequence,)).fetchone()

    def enqueue(self, *, work_id: str, lane_id: str, kind: str, priority: int,
                payload: Dict[str, Any], command_key: str, actor_id: str = "operator") -> dict:
        with self.transaction() as cursor:
            event = self._append(cursor, command_key=command_key, actor_id=actor_id,
                lane_id=lane_id, aggregate_kind="work", aggregate_id=work_id,
                event_type="work.enqueued", payload={"kind": kind, "priority": priority,
                                                      "payload": payload})
            existing = cursor.execute("SELECT * FROM work_items WHERE work_id=?", (work_id,)).fetchone()
            if not existing:
                cursor.execute("INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (work_id, lane_id, kind, "queued", "queued", priority, 1,
                     canonical(payload), event["seq"], event["seq"], event["occurred_at_ms"],
                     event["event_hash"]))
            return self._work(cursor, work_id)

    def _work(self, cursor: sqlite3.Cursor, work_id: str) -> dict:
        row = cursor.execute("SELECT * FROM work_items WHERE work_id=?", (work_id,)).fetchone()
        if not row:
            raise KeyError(work_id)
        value = dict(row); value["payload"] = json.loads(value.pop("payload_json"))
        return value

    def acquire_lease(self, *, resource: str, owner_id: str, ttl_seconds: int,
                      binding: Dict[str, Any], command_key: str,
                      actor_id: Optional[str] = None) -> dict:
        if ttl_seconds <= 0:
            raise ValueError("lease TTL must be positive")
        now_ms = self.clock_ms()
        binding_json = canonical(binding)
        binding_hash = digest(binding)
        with self.transaction() as cursor:
            prior_event = cursor.execute(
                "SELECT * FROM events WHERE command_key=?", (command_key,)
            ).fetchone()
            if prior_event:
                prior_payload = json.loads(prior_event["payload_json"])
                same_intent = bool(
                    prior_event["aggregate_kind"] == "lease" and
                    prior_event["aggregate_id"] == resource and
                    prior_event["event_type"] == "lease.acquired" and
                    prior_event["actor_id"] == (actor_id or owner_id) and
                    prior_payload.get("owner") == owner_id and
                    prior_payload.get("bindingHash") == binding_hash and
                    int(prior_payload.get("ttlSeconds") or 0) == int(ttl_seconds)
                )
                if not same_intent:
                    raise IdempotencyConflict(
                        "command key was already used with different lease input"
                    )
                row = cursor.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone()
                # A retry is idempotent only while the exact acquisition event
                # still owns the current active row.  It may never alias a
                # later renewal, a different unit held by the same owner, or a
                # released/expired lease.
                if (not row or row["owner_id"] != owner_id or
                        int(row["fence"]) != int(prior_payload.get("fence") or 0) or
                        row["binding_hash"] != binding_hash or
                        row["binding_json"] != binding_json or
                        row["phase"] != "active" or
                        int(row["last_event_seq"]) != int(prior_event["seq"]) or
                        int(row["expires_at_ms"]) != int(
                            prior_payload.get("expiresAtMs") or 0
                        ) or int(row["expires_at_ms"]) <= now_ms):
                    raise LeaseLost(
                        "%s acquisition is no longer the active exact lease; "
                        "use an explicit renewal command" % resource
                    )
                return dict(row)
            current = cursor.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone()
            if (resource == "production-writer" and current and
                    current["phase"] in {
                        "publishing", "verifying", "recovery-required",
                        "live-verified", "superseded-no-write",
                    }):
                raise LeaseBusy("production writer requires explicit recovery")
            if current and int(current["expires_at_ms"]) > now_ms and current["owner_id"] != owner_id:
                raise LeaseBusy(resource)
            if current and int(current["expires_at_ms"]) > now_ms and current["binding_hash"] != binding_hash:
                raise LeaseBusy("active lease binding cannot change")
            active_same_owner = bool(
                current and current["owner_id"] == owner_id and
                current["phase"] == "active" and
                int(current["expires_at_ms"]) > now_ms and
                current["binding_hash"] == binding_hash and
                current["binding_json"] == binding_json
            )
            fence = (int(current["fence"]) if active_same_owner else
                     int(current["fence"]) + 1 if current else 1)
            expires_at_ms = now_ms + ttl_seconds * 1000
            event = self._append(cursor, command_key=command_key, actor_id=actor_id or owner_id,
                lane_id=None, aggregate_kind="lease", aggregate_id=resource,
                event_type="lease.acquired", payload={"owner": owner_id, "fence": fence,
                    "bindingHash": binding_hash, "ttlSeconds": int(ttl_seconds),
                    "expiresAtMs": expires_at_ms})
            cursor.execute("INSERT INTO leases (resource,owner_id,fence,expires_at_ms,"
                "binding_hash,binding_json,phase,last_event_seq) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(resource) DO UPDATE SET "
                "owner_id=excluded.owner_id,fence=excluded.fence,expires_at_ms=excluded.expires_at_ms,"
                "binding_hash=excluded.binding_hash,binding_json=excluded.binding_json,"
                "phase=excluded.phase,last_event_seq=excluded.last_event_seq",
                (resource, owner_id, fence, expires_at_ms, binding_hash,
                 binding_json, "active", event["seq"]))
            return dict(cursor.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone())

    def release_lease(self, *, resource: str, owner_id: str, fence: int,
                      command_key: str) -> None:
        if resource == "production-writer":
            raise ValueError("release the production writer through release_writer")
        with self.transaction() as cursor:
            row = cursor.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone()
            if not row or row["owner_id"] != owner_id or int(row["fence"]) != fence:
                raise LeaseLost(resource)
            event = self._append(cursor, command_key=command_key, actor_id=owner_id,
                lane_id=None, aggregate_kind="lease", aggregate_id=resource,
                event_type="lease.released", payload={"owner": owner_id, "fence": fence})
            cursor.execute("UPDATE leases SET phase='released',expires_at_ms=0,last_event_seq=? WHERE resource=?",
                           (event["seq"], resource))

    def acquire_writer(self, *, owner_id: str, binding: Dict[str, Any],
                       free_bytes: int, largest_working_set_bytes: int,
                       ttl_seconds: int, command_key: str) -> dict:
        required = {"taskId", "laneId", "unitId", "catalogSha256",
                    "manuscriptSetSha256", "validationAttestationSha256",
                    "stagingVerificationSha256", "startingLiveRevision",
                    "membershipSha256", "itemCount", "memberIds"}
        missing = sorted(required - set(binding))
        if missing:
            raise ValueError("writer binding is incomplete: %s" % ", ".join(missing))
        for key in ("catalogSha256", "manuscriptSetSha256",
                    "validationAttestationSha256", "stagingVerificationSha256",
                    "membershipSha256"):
            value = str(binding[key])
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.casefold()):
                raise ValueError("%s must be SHA-256" % key)
        member_ids = [str(value) for value in binding["memberIds"]]
        if (not member_ids or len(member_ids) != len(set(member_ids)) or
                member_ids != sorted(member_ids)):
            raise ValueError("writer memberIds must be a nonempty sorted unique list")
        if int(binding["itemCount"]) != len(member_ids):
            raise ValueError("writer itemCount differs from memberIds")
        if not all(str(binding[key]).strip() for key in (
                "taskId", "laneId", "unitId", "startingLiveRevision")):
            raise ValueError("writer identity and live revision fields must be nonempty")
        minimum = max(5 * 1024 ** 3, 2 * int(largest_working_set_bytes))
        if int(free_bytes) <= minimum:
            raise ValueError("writer capacity proof failed")
        return self.acquire_lease(resource="production-writer", owner_id=owner_id,
            ttl_seconds=ttl_seconds, binding={**binding, "capacityProof": {
                "freeBytes": int(free_bytes), "largestWorkingSetBytes": int(largest_working_set_bytes),
                "minimumExclusiveBytes": minimum}}, command_key=command_key)

    def _writer(self, cursor: sqlite3.Cursor, owner_id: str, fence: int) -> sqlite3.Row:
        row = cursor.execute("SELECT * FROM leases WHERE resource='production-writer'").fetchone()
        if not row or row["owner_id"] != owner_id or int(row["fence"]) != int(fence):
            raise LeaseLost("production-writer")
        return row

    @staticmethod
    def _writer_binding(writer: sqlite3.Row) -> Dict[str, Any]:
        try:
            binding = json.loads(writer["binding_json"])
        except (KeyError, TypeError, ValueError) as error:
            raise LeaseLost("production-writer binding is unreadable") from error
        if not isinstance(binding, dict) or not binding.get("unitId"):
            raise LeaseLost("production-writer binding is incomplete")
        return binding

    def begin_publish(self, *, owner_id: str, fence: int, unit_id: str,
                      command_key: str) -> int:
        with self.transaction() as cursor:
            writer = self._writer(cursor, owner_id, fence)
            binding = self._writer_binding(writer)
            if str(unit_id) != str(binding["unitId"]):
                raise ValueError("publication unit differs from the writer binding")
            if writer["phase"] != "active":
                raise ValueError("production writer is not ready to begin publication")
            if int(writer["expires_at_ms"]) <= int(self.clock_ms()):
                raise LeaseLost("production-writer lease expired before publication")
            event = self._append(cursor, command_key=command_key, actor_id=owner_id,
                lane_id="uploader", aggregate_kind="writer", aggregate_id="production-writer",
                event_type="publish.started", payload={"unitId": unit_id, "fence": fence,
                    "executor": "v2-uploader-adapter"})
            cursor.execute("UPDATE leases SET phase='publishing',last_event_seq=? "
                           "WHERE resource='production-writer'", (event["seq"],))
            return int(event["seq"])

    def writer_recovery_required(self, *, owner_id: str, fence: int, reason: str,
                                 command_key: str) -> int:
        with self.transaction() as cursor:
            self._writer(cursor, owner_id, fence)
            event = self._append(cursor, command_key=command_key, actor_id=owner_id,
                lane_id="uploader", aggregate_kind="writer", aggregate_id="production-writer",
                event_type="writer.recovery-required", payload={"reason": reason, "fence": fence})
            cursor.execute("UPDATE leases SET phase='recovery-required',last_event_seq=? "
                           "WHERE resource='production-writer'", (event["seq"],))
            return int(event["seq"])

    def record_live_verification(self, *, owner_id: str, fence: int, unit_id: str,
                                 verified_items: list, receipt: Dict[str, Any],
                                 command_key: str) -> int:
        with self.transaction() as cursor:
            writer = self._writer(cursor, owner_id, fence)
            binding = self._writer_binding(writer)
            if writer["phase"] not in {"publishing", "verifying", "recovery-required"}:
                raise ValueError("publication was not started")
            if str(unit_id) != str(binding["unitId"]):
                raise ValueError("verification unit differs from the writer binding")
            started = any(
                str(payload.get("unitId")) == str(unit_id) and
                int(payload.get("fence") or 0) == int(fence)
                for payload in (
                    json.loads(row["payload_json"])
                    for row in cursor.execute(
                        "SELECT payload_json FROM events "
                        "WHERE aggregate_kind='writer' "
                        "AND aggregate_id='production-writer' "
                        "AND event_type='publish.started'"
                    ).fetchall()
                )
            )
            if not started:
                raise ValueError(
                    "live verification has no matching publication-start event"
                )
            verified = sorted(map(str, verified_items))
            expected = list(binding["memberIds"])
            if verified != expected or len(verified) != int(binding["itemCount"]):
                raise ValueError(
                    "live verification must prove the exact bound membership"
                )
            required_receipt = {
                "itemCount": int(binding["itemCount"]),
                "memberIds": expected,
                "membershipSha256": binding["membershipSha256"],
                "catalogSha256": binding["catalogSha256"],
                "manuscriptSetSha256": binding["manuscriptSetSha256"],
                "validationAttestationSha256": binding[
                    "validationAttestationSha256"
                ],
                "stagingVerificationSha256": binding[
                    "stagingVerificationSha256"
                ],
            }
            for key, value in required_receipt.items():
                if receipt.get(key) != value:
                    raise ValueError(
                        "live verification receipt does not prove bound %s" % key
                    )
            event = self._append(cursor, command_key=command_key, actor_id=owner_id,
                lane_id="uploader", aggregate_kind="writer", aggregate_id="production-writer",
                event_type="publish.live-verified", payload={"unitId": unit_id,
                    "verifiedItems": verified, "receipt": receipt,
                    "fence": fence})
            cursor.execute("UPDATE leases SET phase='live-verified',last_event_seq=? "
                           "WHERE resource='production-writer'", (event["seq"],))
            return int(event["seq"])

    def release_writer(self, *, owner_id: str, fence: int, command_key: str) -> None:
        with self.transaction() as cursor:
            writer = self._writer(cursor, owner_id, fence)
            if writer["phase"] not in {"live-verified", "superseded-no-write"}:
                raise ValueError("writer release requires live verification or verified no-write convergence")
            event = self._append(cursor, command_key=command_key, actor_id=owner_id,
                lane_id="uploader", aggregate_kind="writer", aggregate_id="production-writer",
                event_type="writer.released", payload={"fence": fence,
                    "outcome": writer["phase"]})
            cursor.execute("UPDATE leases SET phase='released',expires_at_ms=0,last_event_seq=? "
                           "WHERE resource='production-writer'", (event["seq"],))

    def record_heartbeat(self, *, work_id: str, actor_id: str,
                         telemetry: Dict[str, Any], command_key: str) -> int:
        with self.transaction() as cursor:
            event = self._append(cursor, command_key=command_key, actor_id=actor_id,
                lane_id=self._work(cursor, work_id)["lane_id"], aggregate_kind="work",
                aggregate_id=work_id, event_type="work.heartbeat", payload=telemetry)
            cursor.execute("UPDATE work_items SET last_event_seq=? WHERE work_id=?",
                           (event["seq"], work_id))
            return int(event["seq"])

    def record_progress(self, *, work_id: str, actor_id: str, proof_kind: str,
                        evidence: Dict[str, Any], command_key: str,
                        pivot_id: Optional[str] = None) -> int:
        if proof_kind not in MEANINGFUL_PROOFS:
            raise ProgressRejected("telemetry is not a meaningful progress proof")
        fingerprint = digest({"kind": proof_kind, "evidence": evidence})
        with self.transaction() as cursor:
            work = self._work(cursor, work_id)
            if fingerprint == work["progress_fingerprint"]:
                raise ProgressRejected("progress proof did not change durable evidence")
            event = self._append(cursor, command_key=command_key, actor_id=actor_id,
                lane_id=work["lane_id"], aggregate_kind="work", aggregate_id=work_id,
                event_type="work.progressed", payload={"proofKind": proof_kind,
                    "evidence": evidence, "pivotId": pivot_id})
            cursor.execute("UPDATE work_items SET last_event_seq=?,last_progress_seq=?,"
                "last_progress_ms=?,progress_fingerprint=?,version=version+1 WHERE work_id=?",
                (event["seq"], event["seq"], event["occurred_at_ms"], fingerprint, work_id))
            if pivot_id:
                pivot = cursor.execute("SELECT * FROM pivots WHERE pivot_id=? AND work_id=?",
                                       (pivot_id, work_id)).fetchone()
                if not pivot or pivot["status"] != "acknowledged" or event["seq"] <= pivot["baseline_seq"]:
                    raise ProgressRejected("pivot success proof is not causally valid")
                cursor.execute("UPDATE pivots SET status='succeeded',succeeded_at_ms=?,success_seq=? WHERE pivot_id=?",
                               (event["occurred_at_ms"], event["seq"], pivot_id))
            return int(event["seq"])

    def evaluate_pivots(self, *, stale_after_seconds: int = 600,
                        success_after_seconds: int = 600) -> dict:
        now_ms = self.clock_ms(); requested = []; escalated = []
        with self.transaction() as cursor:
            for work in cursor.execute("SELECT * FROM work_items WHERE status IN ('queued','running')").fetchall():
                open_pivot = cursor.execute("SELECT * FROM pivots WHERE work_id=? AND status IN "
                    "('requested','acknowledged')", (work["work_id"],)).fetchone()
                if open_pivot:
                    if now_ms - int(open_pivot["requested_at_ms"]) >= success_after_seconds * 1000:
                        key = "pivot-escalate:%s" % open_pivot["pivot_id"]
                        event = self._append(cursor, command_key=key, actor_id="pivot-enforcer",
                            lane_id=work["lane_id"], aggregate_kind="pivot",
                            aggregate_id=open_pivot["pivot_id"], event_type="pivot.escalated",
                            payload={"workId": work["work_id"], "attempt": open_pivot["attempt"]})
                        cursor.execute("UPDATE pivots SET status='escalated',escalated_at_ms=? WHERE pivot_id=?",
                                       (event["occurred_at_ms"], open_pivot["pivot_id"]))
                        escalated.append(open_pivot["pivot_id"])
                    continue
                if now_ms - int(work["last_progress_ms"]) < stale_after_seconds * 1000:
                    continue
                attempt_row = cursor.execute("SELECT COALESCE(MAX(attempt),0) FROM pivots WHERE work_id=?",
                                             (work["work_id"],)).fetchone()
                attempt = int(attempt_row[0]) + 1
                pivot_id = digest({"work": work["work_id"], "baseline": work["last_progress_seq"],
                                   "attempt": attempt})[:24]
                key = "pivot-request:%s" % pivot_id
                event = self._append(cursor, command_key=key, actor_id="pivot-enforcer",
                    lane_id=work["lane_id"], aggregate_kind="pivot", aggregate_id=pivot_id,
                    event_type="pivot.requested", payload={"workId": work["work_id"],
                        "baselineSeq": work["last_progress_seq"], "attempt": attempt})
                cursor.execute("INSERT INTO pivots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pivot_id, work["work_id"], work["lane_id"], work["last_progress_seq"],
                     work["progress_fingerprint"], event["occurred_at_ms"], None, None, None,
                     None, None, None, attempt, "requested"))
                requested.append(pivot_id)
        return {"requested": requested, "escalated": escalated,
                "staleAfterSeconds": stale_after_seconds}

    def acknowledge_pivot(self, *, pivot_id: str, actor_id: str, action_family: str,
                          checkpoint_sha256: str, command_key: str) -> None:
        if len(checkpoint_sha256) != 64:
            raise ValueError("checkpoint SHA-256 is required")
        with self.transaction() as cursor:
            pivot = cursor.execute("SELECT * FROM pivots WHERE pivot_id=?", (pivot_id,)).fetchone()
            if not pivot or pivot["status"] != "requested":
                raise ValueError("pivot is not awaiting acknowledgment")
            previous = cursor.execute("SELECT action_family FROM pivots WHERE work_id=? AND status='escalated' "
                "ORDER BY attempt DESC LIMIT 1", (pivot["work_id"],)).fetchone()
            if previous and previous[0] == action_family:
                raise ValueError("escalated pivot must use a materially different action family")
            event = self._append(cursor, command_key=command_key, actor_id=actor_id,
                lane_id=pivot["lane_id"], aggregate_kind="pivot", aggregate_id=pivot_id,
                event_type="pivot.acknowledged", payload={"actionFamily": action_family,
                    "checkpointSha256": checkpoint_sha256})
            cursor.execute("UPDATE pivots SET status='acknowledged',action_family=?,"
                "checkpoint_sha256=?,acknowledged_at_ms=? WHERE pivot_id=?",
                (action_family, checkpoint_sha256, event["occurred_at_ms"], pivot_id))

    def snapshot(self) -> dict:
        with self.db:
            events = [dict(row) for row in self.db.execute("SELECT * FROM events ORDER BY seq")]
            previous = ZERO_HASH
            for row in events:
                payload = json.loads(row["payload_json"])
                envelope = {"seq": row["seq"], "commandHash": row["command_hash"],
                    "occurredAtMs": row["occurred_at_ms"], "actor": row["actor_id"],
                    "lane": row["lane_id"], "aggregateKind": row["aggregate_kind"],
                    "aggregateId": row["aggregate_id"], "eventType": row["event_type"],
                    "payload": payload, "previousHash": previous}
                if row["previous_hash"] != previous or row["event_hash"] != digest(envelope):
                    raise ValueError("Ultra event chain verification failed")
                previous = row["event_hash"]
            work = [dict(row) for row in self.db.execute("SELECT * FROM work_items ORDER BY work_id")]
            leases = [dict(row) for row in self.db.execute("SELECT * FROM leases ORDER BY resource")]
            pivots = [dict(row) for row in self.db.execute("SELECT * FROM pivots ORDER BY requested_at_ms,pivot_id")]
        state = {"work": work, "leases": leases, "pivots": pivots}
        return {"throughEventSeq": events[-1]["seq"] if events else 0,
                "headEventHash": previous, "state": state, "stateSha256": digest(state)}
