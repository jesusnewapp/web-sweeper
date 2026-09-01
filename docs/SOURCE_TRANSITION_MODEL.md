# Source Transition Model

Web Sweeper may transition a source slot only at a completed-unit boundary.
The old source finishes its active unit, writes an exact non-production staging
receipt, cleans only receipt-bound re-downloadable cache, and preserves its
checkpoint. The controller then confirms the old coordinator is inactive before
starting exactly one configured successor and recording a durable transition
receipt. A restart replays the receipt instead of starting both sources.

Set `source_slot_count` to the number of ordered lanes required and provide
exactly that many entries in `source_slots`. Slot numbers must be consecutive,
source IDs must be unique, and every slot has its own acquisition target and
configured command. The supplied practice pool contains ten slots. A deployment
may reduce it to one, two, or five slots without changing the state machine.

The first three practice slots are the reference lanes: Open Library, Internet
Archive, and Library of Congress. Seven additional source boundaries reuse the
proven Internet Archive adapter: American Libraries, University of Toronto,
Internet Archive Books, California Digital Library, Medical Heritage Library,
European Libraries, and Biodiversity Heritage Library. They are distinct
collection boundaries, not new adapter dependencies. Discovery uses Internet
Archive's public search API and manuscript acquisition uses the selected item's
public direct-download derivative.

The seven supplied queries were measured on 2026-08-12 with all of these terms:
`mediatype:texts`, English, publication year no later than 1930, `DjVuTXT`
present, and no restricted-access flag. Their observed boundaries were
1,299,926; 297,069; 248,107; 182,672; 168,166; 133,604; and 105,970 records,
respectively. These counts establish the discovery boundary; every individual
item still passes rights, completeness, relevance, and global duplicate gates.

Every configured source must offer a public complete-text download without a
login, loan, manual approval, or browser-only step. Restricted records are
ineligible. Google Books and restricted HathiTrust records are intentionally
absent. Plymouth Brethren and Project Gutenberg remain useful bounded sources,
but they are not included in this reference pool because they do not meet the
current 100,000-work source floor. Collection overlap is resolved with prior-identifier and content-hash
exclusions, so a later slot cannot inflate its yield with earlier works.

Each route targets 1,000 accepted works. If its bounded source exhausts first,
the source writes `sourceExhausted: true` into the exact completion receipt and
the controller stages every positive survivor count before transitioning. An
empty result is not staged, and a partial unit without exhaustion evidence is
rejected rather than mistaken for completion.

`plan_slot_continuation` defines the automatic decision at that boundary. A
full non-exhausted unit restarts the current source from its checkpoint. Proven
exhaustion stages a positive remainder, retires the source, and advances to the
next slot. Proven empty exhaustion retires and advances without inventing a
staging receipt. The final exhausted slot completes the pool. The controller
must still bind that decision to exact receipts and confirm the old process is
dead before launching the configured successor.

The practice throughput marker is based on the measured Internet Archive run:
7,186 accepted works in 558.46 acquisition minutes, or approximately 466
seconds per 100 accepts. The example marks a source `slow-right-now` when a
complete 100-accept observation exceeds twice that baseline: 932 seconds (15
minutes 32 seconds) per 100. A slow mark schedules transition at the next exact
receipt/checkpoint boundary. It never kills an in-flight fetch or discards a
positive accepted remainder. Deployments should replace the baseline with their
own measured reference lane when hardware, network, or source behavior differs.

Publication sizing is independent from acquisition sizing. Set the global
`publication_policy.desired_batch_size` or a source-level
`desired_publication_batch_size` placeholder to an integer from 1 through
1,000. For example, a source may acquire and stage 1,000 works, then the one
serialized writer may publish ten exact 100-work units. A smaller positive final
remainder is its own publication unit. Every unit receives a fresh live
duplicate delta and exact live verification before the writer advances.

Completed or exhausted sources must not be recycled.

Evaluate the transition evidence without executing source commands:

```bash
PYTHONPATH=src python3 -m sweeper.transition \
  --config examples/source-transition.practice.json
```

The evaluator never starts, stops, downloads, stages, or publishes anything. It
reports `waiting-for-current-unit` until all three evidence files exist and then
reports `ready-to-transition`. The exported continuation planner makes the next
action deterministic; process supervision remains deployment-specific.
