# Web Sweeper Developer Interface

The Developer Interface is a simple cross-platform control surface for Web Sweeper. It shows lane health, current stages, accepted and uploaded counts, Codex Live totals supplied by the controller, source and batch settings, and explicitly configured recovery actions.

It includes:

- one Flutter codebase with Android, iOS, macOS, Windows, Linux, and web targets;
- a dependency-free Python/Tk desktop companion;
- an authenticated Python controller bridge for desktop and mobile clients;
- configurable readable-text colors;
- green, yellow, orange, and red lane states;
- a color-coded `Gate X/Y · Ns` sweep signal on every lane (six acquisition gates and seven publisher gates);
- exact counters that refresh independently from the gate timer;
- accepted-growth health that never mistakes retrieval activity or a heartbeat
  for newly accepted material;
- cache-free controller responses so a UI reset or background refresh requests
  the current authoritative stage and staging custody;
- an elapsed timer after the complete durable progress vector remains unchanged for 10 seconds;
- a per-lane Push control that remains disabled until five minutes without any progress evidence, then invokes only the host-configured trusted `push` action;
- a four-pad waiting game with a persistent high score and one-time round-20 message;
- one serialized production-writer invariant.

## Flutter

```bash
cd flutter
flutter pub get
flutter run
```

Choose the target with `flutter devices` and `flutter run -d <device>`. The shared UI is responsive; platform folders are provided for Android, iOS, macOS, Windows, Linux, and web.

## Python desktop app

```bash
python3 python/developer_interface.py
```

## Controller bridge

Copy `python/controller.example.json`, set its `projectRoot` and lane state paths, and add only the trusted action commands the operator intends to expose. Arbitrary commands from clients are never accepted.

Local desktop use:

```bash
export WEB_SWEEPER_TOKEN='replace-with-a-long-random-token'
python3 python/server.py --config /path/to/controller.json
```

For the simplest same-Mac connection, `--local-no-auth` accepts only loopback
clients and cannot be combined with a network-facing host. Remote and mobile
connections still require HTTPS and a token.

For a local service, the token may instead be kept in a mode-`0600` file and
passed with `--token-file`. A configured `metricsPath` supplies live global
counts such as `codexLive` and `confirmedStaged` without embedding project
specific storage credentials in the UI.

Mobile access must use HTTPS:

```bash
export WEB_SWEEPER_TOKEN='replace-with-a-long-random-token'
python3 python/server.py \
  --config /path/to/controller.json \
  --host 0.0.0.0 \
  --cert /path/to/fullchain.pem \
  --key /path/to/private-key.pem
```

Enter that HTTPS URL and token in the Android or iOS app. Tokens are stored only in the device's application preferences and are not part of public source. For an internet-facing deployment, use a maintained TLS reverse proxy, firewall rules, token rotation, and a private network or VPN.

The client retries its configured controller every five seconds even when its
first cold-start request fails. A brief local-service outage may show preview
placeholders, but it cannot strand the interface there; live counters restore
automatically when the controller returns.

For a long-running local desktop session, `python/supervise_controller.py`
restarts the controller after any unexpected nonzero exit. Run it under the
host's normal user-session supervisor so it retains the same filesystem access
as the operator; do not bypass operating-system privacy controls. A deliberate
clean server exit ends the supervisor, while a direct keyboard interrupt shuts
down its child cleanly.

## Safety contract

- Status is read from configured state/checkpoint/receipt files; a PID alone is not treated as progress.
- Acquisition health is based only on monotonic authoritative accepted growth.
  Retrieved, screened, attempted, and heartbeat counts remain useful diagnostics,
  but cannot label a stagnant lane healthy.
- An active Opti review adapter remains authoritative even when its current root
  has an older staging or live-verification receipt. The receipt stays in Success
  History while the operating card shows the active journal count, review stage,
  candidate remainder, and accepted-growth time.
- Timestamp precedence is explicit: stale staging progress cannot replace a newer
  acquisition state after a source-bound closeout or recovery.
- Inactivity is multi-signal: accepted, discovery-page/cursor, candidate-inventory,
  stage, upload, publication, verification, checkpoint timestamp, and receipt
  movement all count. A quiet accepted counter cannot terminate active discovery.
- Source adapters whose page journal is separate from their lane state can list
  trusted `progressPaths`; file-size or modification movement then resets the
  progress clock without parsing or exposing the journal contents.
- A recent discovery journal presents the lane explicitly as `discovery` with
  “moving smoothly” status. Its controller heartbeat is grouped as a 30-second
  checkpoint signal while exact accepted counts remain unchanged.
- Acquisition cards expose a pressable **Discovery Mode** or **Uploading Mode**
  pill with exact page, candidate, checkpoint, and upload details. Large
  discovery journals are sampled no more than once every 30 seconds. During
  discovery, the card foregrounds unique pages completed, candidate inventory,
  and accepted survivors instead of displaying a misleading zero-upload line.
  Recent query movement is computed from newly completed page keys, so lexical
  checkpoint ordering cannot look like a repeated request cycle.
- Every source card uses the same gate contract while foregrounding the counter
  that can actually move: discovery shows pages scanned plus candidates and
  accepted survivors; acquisition shows accepted versus target; staging upload
  shows uploaded versus its exact receipt target. A prior accepted count is
  never presented as the active discovery counter.
- A multi-checkpoint campaign may set `displayCumulativeProgress` on its lane.
  The card then retains accepted custody across checkpoint changes and names
  the split explicitly: total campaign custody, protected prior-unit books,
  and books accepted by the currently acquiring unit. Moving books to staging
  or publisher custody cannot make the campaign counter fall back to zero.
- Every bounded loading phase may expose `gateProgressCurrent` and
  `gateProgressTarget`. The interface renders a separate exact 0–100% meter for
  that phase. It never manufactures a percentage when an adapter has not
  supplied a trustworthy denominator.
- Cards render the major pipeline sequentially. Source lanes advance through
  initialize → discover/acquire → validate → staging upload → staging
  verification → complete. Publishers advance through queue/preflight → fresh
  live duplicate delta → dedup/prepare → storage upload → publish → live
  verification → complete. Only the active gate's exact work meter is shown;
  completed and future gates are represented by the pipeline meter. A lane
  never renders two competing active-work meters.
- A discovery frontier may carry already accepted survivors forward within the
  same logical batch. The card labels that count as **survivors carried
  forward** so it is not mistaken for the discovery-page counter or a new
  staging upload.
- The publisher exposes **Verification Mode** or **Uploading Mode**. Its
  dismissible details show the exact gate, receipts, counts, queue state, and
  timestamps while upload counts remain visible on the card.
- Raw adapter stage names such as `prepare` are kept inside the detail view.
  The card instead shows a glowing **Active** status and a pressable Discovery,
  Verification, or Uploading control. Discovery and verification controls show
  the exact unchanged-evidence age and scale yellow to orange to red at five
  minutes; any durable counter, journal, receipt, or timestamp movement resets
  that clock.
- A receipt-complete acquisition unit keeps a rainbow **Staged** state until
  its lane resets. A publication and live-verification-complete unit keeps a
  rainbow **Published** state until the serialized publisher selects its next
  unit. These states come from exact completion evidence, not attempted counts.
- Production publishing remains limited to one serialized writer.
- UI actions invoke only host-configured commands and cannot accept shell text from the client.
- Actions are disabled by default in the public example.
- Push is a continuation request, not a permission bypass: no eligible staged unit means nothing is published.
- The interface does not bypass duplicate screening, publication receipts, or live verification.
- Staged, uploaded, published, and live-verified counts remain distinct.
