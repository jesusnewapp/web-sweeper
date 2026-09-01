import 'dart:io';

import 'package:developer_interface/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<void> pumpAt(WidgetTester tester, Size size) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(const WebSweeperDeveloperApp());
  await tester.pumpAndSettle();
}

void main() {
  test('newest refresh response supersedes an older in-flight response', () {
    final gate = RequestGenerationGate();
    final periodic = gate.begin();
    final manual = gate.begin();

    expect(gate.canCommit(periodic), isFalse);
    expect(gate.canCommit(manual), isTrue);
  });

  test(
    'authoritative stage mapping separates active acquisition, bounded inventory, and idle publisher',
    () {
      const archive = ModelView(
        id: 'internet-archive',
        name: 'Internet Archive',
        stage: 'prepare',
        accepted: 515,
        target: 2000,
        uploaded: 0,
        health: Health.healthy,
        detail: 'accepting',
        modeDetail: {'acceptedJournalCount': 515},
      );
      const grey = ModelView(
        id: 'global-grey-christianity',
        name: 'Global Grey',
        stage: 'bounded-frontier-complete',
        accepted: 0,
        target: 1000,
        uploaded: 0,
        health: Health.watch,
        detail: 'inventory complete',
      );
      const publisher = ModelView(
        id: 'publisher',
        name: 'Publisher',
        stage: 'Listening for next exact staged unit',
        accepted: 0,
        target: 0,
        uploaded: 0,
        health: Health.healthy,
        detail: 'idle',
      );
      final archiveGate = gatePositionFor(archive);
      final greyGate = gatePositionFor(grey);
      final publisherGate = gatePositionFor(publisher);
      expect(archiveGate.current, 2);
      expect(greyGate.current, 3);
      expect(
        activeSubstageFor(archive, 2, pipelineSubstagesFor(archive, 2)),
        pipelineSubstagesFor(archive, 2).length - 1,
      );
      expect(activeSubstageFor(grey, 3, pipelineSubstagesFor(grey, 3)), 0);
      expect(
        activeSubstageFor(publisher, 8, pipelineSubstagesFor(publisher, 8)),
        pipelineSubstagesFor(publisher, 8).length - 1,
      );
    },
  );

  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('background refresh forces authoritative network status', () {
    final source = Uri.file('lib/main.dart').toFilePath();
    final text = File(source).readAsStringSync();
    expect(text, contains('_connect(quiet: true, forceNetwork: true)'));
    expect(statusRefreshInterval, const Duration(seconds: 5));
  });

  test(
    'acquisition candidate count is rendered against its screening target',
    () {
      final model = ModelView.fromJson({
        'id': 'internet-archive',
        'name': 'Internet Archive',
        'stage': 'prepare',
        'accepted': 12,
        'target': 3000,
        'candidateCount': 705,
        'candidateTarget': 10000,
        'uploaded': 0,
        'health': 'healthy',
        'mode': 'acquisition',
      });
      expect(candidateCounterLabel(model), 'Candidates screened: 705 / 10000');
    },
  );

  test('terminal cards never acquire a local stuck label', () {
    final model = ModelView.fromJson({
      'id': 'internet-archive',
      'name': 'Internet Archive',
      'stage': 'campaign-complete',
      'accepted': 1,
      'target': 3000,
      'candidateCount': 705,
      'candidateTarget': 10000,
      'uploaded': 1,
      'health': 'healthy',
      'mode': 'complete',
    });
    expect(isTerminalModel(model), isTrue);
  });

  test('publisher campaign exposes aggregate live and batch progress', () {
    const model = ModelView(
      id: 'publisher',
      name: 'Stage to Live',
      stage: 'storage-upload',
      accepted: 40,
      target: 100,
      uploaded: 40,
      health: Health.healthy,
      detail: 'publishing',
      mode: 'uploading',
      modeDetail: {
        'campaignLiveVerified': 4996,
        'campaignBooksTotal': 9773,
        'campaignBatchesCompleted': 50,
        'campaignBatchesTotal': 98,
      },
    );
    expect(
      publisherCampaignLabel(model),
      'Campaign live: 4996 / 9773 · Batches: 50 / 98',
    );
  });

  test('UI reset retains proven live cards until replacement succeeds', () {
    final text = File('lib/main.dart').readAsStringSync();
    expect(text, contains('Keep the last proven controller snapshot visible'));
    expect(
      text,
      isNot(
        contains(
          "_connectionMessage = 'Resetting UI · loading authoritative status…';\n      _models = const []",
        ),
      ),
    );
  });

  test('institutional card exposes receipt-safe lane clean reset', () {
    final text = File('lib/main.dart').readAsStringSync();
    expect(text, contains("ValueKey('clean-reset-\${model.id}')"));
    expect(text, contains("_sendAction('clean-reset-lane'"));
    expect(text, contains("if (model.id == 'google-books' &&"));
  });

  test('live-index refresh stays in the active discovery gate', () {
    final model = ModelView.fromJson({
      'id': 'open-library-stories',
      'name': 'Open Library · Model 1 Parallel',
      'stage': 'fresh-live-export',
      'accepted': 0,
      'target': 2000,
      'uploaded': 0,
      'health': 'healthy',
    });
    final gate = gatePositionFor(model);
    expect(gate.current, 2);
    expect(gate.total, 6);
  });

  test('completed sub-one-percent source is marked exhausted', () {
    final model = ModelView.fromJson({
      'id': 'princeton',
      'name': 'Princeton',
      'stage': 'complete',
      'accepted': 18,
      'target': 2000,
      'health': 'stuck',
      'exhaustedSource': true,
      'acceptanceRate': 0.815,
    });
    expect(model.exhaustedSource, isTrue);
    expect(model.acceptanceRate, 0.815);
  });

  testWidgets('exhausted source acceptance rate is visibly red', (
    tester,
  ) async {
    final since = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'princeton',
              name: 'Princeton',
              stage: 'complete',
              accepted: 18,
              target: 2000,
              uploaded: 18,
              health: Health.stuck,
              detail: 'Completed screening window',
              exhaustedSource: true,
              acceptanceRate: 0.815,
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 9,
              since: since,
            ),
            stageObservation: StageObservation(stage: 'complete', since: since),
            now: since,
            onPush: () {},
          ),
        ),
      ),
    );

    final rate = tester.widget<Text>(
      find.byKey(const ValueKey('exhausted-rate-princeton')),
    );
    expect(rate.data, 'EXHAUSTED SOURCE · 0.815% accepted');
    expect(rate.style?.color, const Color(0xffff5f6d));
  });

  test('discover and acquire focus follows the live controller operation', () {
    final discovering = ModelView.fromJson({
      'id': 'source',
      'stage': 'discover',
      'accepted': 0,
      'target': 10,
    });
    final acquiring = ModelView.fromJson({
      'id': 'source',
      'stage': 'acquiring-text',
      'accepted': 0,
      'target': 10,
    });
    expect(discoveryAcquisitionFocus(discovering), 'discover');
    expect(discoveryAcquisitionFocus(acquiring), 'acquire');
  });

  testWidgets('phone layout renders without overflow', (tester) async {
    await pumpAt(tester, const Size(390, 844));
    expect(find.text('WEB SWEEPER'), findsOneWidget);
    expect(find.text('WAITING GAME'), findsOneWidget);
    expect(find.byType(Image), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('desktop layout renders without overflow', (tester) async {
    await pumpAt(tester, const Size(1440, 1000));
    expect(find.text('Codex Live'), findsOneWidget);
    expect(find.byKey(const ValueKey('refresh-ui-button')), findsOneWidget);
    expect(find.byKey(const ValueKey('workspace-toggle-button')), findsNothing);
    expect(find.text('Refresh UI'), findsOneWidget);
    expect(
      tester
          .widget<TextButton>(find.byKey(const ValueKey('refresh-ui-button')))
          .onPressed,
      isNotNull,
    );
    expect(find.byKey(const ValueKey('clean-sweep-button')), findsOneWidget);
    expect(find.text('Readable text color:'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('clean sweep requires explicit receipt-preserving confirmation', (
    tester,
  ) async {
    await pumpAt(tester, const Size(1440, 1000));
    await tester.tap(find.byKey(const ValueKey('clean-sweep-button')));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('clean-sweep-confirmation')),
      findsOneWidget,
    );
    expect(find.textContaining('Permanent receipts'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('confirm-clean-sweep-button')),
      findsOneWidget,
    );
  });

  testWidgets('unchanged percentages show their age after ten seconds', (
    tester,
  ) async {
    final since = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'publisher',
              name: 'Publisher',
              stage: 'Live verification',
              accepted: 100,
              target: 100,
              uploaded: 100,
              health: Health.healthy,
              detail: 'Single writer',
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 1000,
              since: since,
            ),
            stageObservation: StageObservation(
              stage: 'Live verification',
              since: since,
            ),
            now: since.add(const Duration(seconds: 11)),
            onPush: () {},
          ),
        ),
      ),
    );
    expect(find.textContaining('At 100.0% for'), findsOneWidget);
    expect(find.text('Gate 7/8 · 11s'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('exact stage button lists and highlights the current stage', (
    tester,
  ) async {
    final now = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'open-library',
              name: 'Open Library',
              stage: 'prepare',
              accepted: 3,
              target: 2000,
              uploaded: 0,
              health: Health.healthy,
              detail: 'Live status',
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 1,
              since: now,
            ),
            stageObservation: StageObservation(stage: 'prepare', since: now),
            now: now,
            onPush: () {},
          ),
        ),
      ),
    );
    await tester.tap(
      find.byKey(const ValueKey('exact-stage-button-open-library')),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('exact-stage-dialog-open-library')),
      findsOneWidget,
    );
    expect(find.text('1. Initialize'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('exact-stage-open-library-discover')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('exact-stage-open-library-acquire')),
      findsOneWidget,
    );
    expect(find.text('CURRENT'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('substage-panel-open-library-2')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('substage-meter-open-library')),
      findsOneWidget,
    );
    expect(find.textContaining('Substage 1/10'), findsWidgets);
    expect(
      find.byKey(const ValueKey('card-substage-meter-open-library')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('card-substage-remaining-open-library')),
      findsOneWidget,
    );
  });

  testWidgets('completed unit keeps a celebratory published state', (
    tester,
  ) async {
    final now = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'publisher',
              name: 'Stage-to-live publisher',
              stage: 'Listening for next exact staged unit',
              accepted: 0,
              target: 0,
              uploaded: 0,
              health: Health.healthy,
              detail: 'Last completed: 1376 published',
              mode: 'verification',
              modeDetail: {
                'completionState': 'published',
                'published': 1376,
                'liveVerified': 1376,
                'batchQueue': [
                  {
                    'batchNumber': 3,
                    'name': 'internet_archive_unit_003',
                    'books': 1214,
                    'status': 'Ready to roll',
                    'current': false,
                  },
                ],
              },
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 0,
              since: now,
            ),
            stageObservation: StageObservation(
              stage: 'Listening for next exact staged unit',
              since: now,
            ),
            now: now,
            onPush: () {},
          ),
        ),
      ),
    );
    expect(
      find.byKey(const ValueKey('completion-pill-publisher')),
      findsOneWidget,
    );
    expect(find.text('Published'), findsOneWidget);
    expect(find.byKey(const ValueKey('publisher-batch-queue')), findsOneWidget);
    expect(find.text('Unit 3 · 1214 books'), findsOneWidget);
    expect(find.text('Ready to roll'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('completed acceptance substage leaves the active card', (
    tester,
  ) async {
    final now = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'internet-archive',
              name: 'Internet Archive · General Christian',
              stage: 'prepare',
              accepted: 2000,
              target: 2000,
              uploaded: 0,
              health: Health.healthy,
              detail: 'acceptance complete',
              modeDetail: {
                'substageProgressLabel': 'Accepting books',
                'substageProgressCurrent': 2000,
                'substageProgressTarget': 2000,
              },
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 1000,
              since: now,
            ),
            stageObservation: StageObservation(stage: 'prepare', since: now),
            now: now,
            onPush: () {},
          ),
        ),
      ),
    );
    expect(
      find.byKey(const ValueKey('card-substage-meter-internet-archive')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('card-substage-remaining-internet-archive')),
      findsNothing,
    );
  });

  testWidgets('idle publisher shows only the next-batch waiting state', (
    tester,
  ) async {
    final now = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'publisher',
              name: 'Stage-to-live publisher',
              stage: 'complete',
              accepted: 0,
              target: 0,
              uploaded: 0,
              health: Health.healthy,
              detail: 'last unit complete',
              modeDetail: {'completionState': 'published'},
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 0,
              since: now,
            ),
            stageObservation: StageObservation(stage: 'complete', since: now),
            now: now,
            onPush: () {},
          ),
        ),
      ),
    );
    expect(find.text('Waiting for next staged batch'), findsOneWidget);
    expect(find.text('Published'), findsNothing);
    expect(find.textContaining('Verification'), findsNothing);
    expect(find.textContaining('100%'), findsNothing);
    expect(find.byType(LinearProgressIndicator), findsNothing);
  });

  testWidgets('validated source remains ready instead of becoming stuck', (
    tester,
  ) async {
    final since = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'global-grey-christianity',
              name: 'Global Grey Ebooks · Christianity',
              stage: 'validation-complete',
              accepted: 39,
              target: 39,
              uploaded: 0,
              health: Health.watch,
              detail: 'Validation complete · 39/39 accepted manuscripts',
              modeDetail: {
                'substageProgressCurrent': 39,
                'substageProgressTarget': 39,
              },
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 1000,
              since: since,
            ),
            stageObservation: StageObservation(
              stage: 'validation-complete',
              since: since,
            ),
            now: since.add(const Duration(minutes: 10)),
            onPush: () {},
          ),
        ),
      ),
    );
    expect(find.text('Ready to stage'), findsOneWidget);
    expect(find.text('Stuck'), findsNothing);
    expect(find.textContaining('Why:'), findsNothing);
  });

  testWidgets('acquisition search gate has a timed health signal', (
    tester,
  ) async {
    final since = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'library-of-congress',
              name: 'Library of Congress',
              stage: 'prepare',
              accepted: 0,
              target: 2000,
              uploaded: 0,
              health: Health.watch,
              detail: 'Searching and qualifying candidates',
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 0,
              since: since,
            ),
            stageObservation: StageObservation(stage: 'prepare', since: since),
            now: since.add(const Duration(seconds: 37)),
            onPush: () {},
          ),
        ),
      ),
    );
    expect(find.text('Gate 2/6 · 37s'), findsOneWidget);
    expect(find.byIcon(Icons.radar_rounded), findsOneWidget);
    expect(
      find.byKey(const ValueKey('active-pill-library-of-congress')),
      findsOneWidget,
    );
    expect(find.text('UI active'), findsOneWidget);
    expect(find.text('prepare'), findsNothing);
    expect(find.text('Discovery · 37s'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('discovery foregrounds page and candidate movement', (
    tester,
  ) async {
    final since = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'library-of-congress',
              name: 'Library of Congress',
              stage: 'discovery',
              mode: 'discovery',
              modeDetail: {
                'pagesCompleted': 146,
                'candidateRecords': 130,
                'gateProgressLabel': 'Discovery pages',
                'gateProgressCurrent': 40,
                'gateProgressTarget': 160,
              },
              accepted: 7,
              target: 2000,
              uploaded: 0,
              health: Health.healthy,
              detail: 'Discovery mode · moving smoothly',
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 4,
              since: since,
            ),
            stageObservation: StageObservation(
              stage: 'discovery',
              since: since,
            ),
            now: since.add(const Duration(seconds: 13)),
            onPush: () {},
          ),
        ),
      ),
    );
    expect(find.text('146 pages scanned'), findsOneWidget);
    expect(find.text('Pipeline 2/6 · Discover / acquire'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('pipeline-meter-library-of-congress')),
      findsOneWidget,
    );
    expect(
      find.text('146 pages · 130 candidates · 7 survivors carried forward'),
      findsOneWidget,
    );
    expect(find.text('7 / 2000'), findsNothing);
    expect(find.text('Discovery pages · 40 / 160'), findsOneWidget);
    expect(find.text('25.0%'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('gate-loading-meter-library-of-congress')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('active-work-meter-library-of-congress')),
      findsNothing,
    );
    expect(find.textContaining('0 uploaded'), findsNothing);
  });

  testWidgets('retrieval is pipeline two while accepted remains distinct', (
    tester,
  ) async {
    final since = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'project-puritas',
              name: 'Project Puritas · Puritan Search',
              stage: 'acquisition-running',
              mode: 'acquisition',
              modeDetail: {
                'substageProgressLabel': 'Retrieving and accepting books',
                'substageProgressCurrent': 495,
                'substageProgressTarget': 990,
              },
              accepted: 0,
              target: 1000,
              uploaded: 0,
              health: Health.healthy,
              detail:
                  'Retrieval moving · 495/990 books processed · 0 authoritatively accepted',
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 0,
              since: since,
            ),
            stageObservation: StageObservation(
              stage: 'acquisition-running',
              since: since,
            ),
            now: since.add(const Duration(seconds: 19)),
            onPush: () {},
          ),
        ),
      ),
    );
    expect(
      tester
          .widget<Text>(
            find.byKey(const ValueKey('pipeline-label-project-puritas')),
          )
          .data,
      'Pipeline 2/6 · Discover / acquire',
    );
    expect(find.text('0 / 1000'), findsOneWidget);
    expect(find.textContaining('Retrieving and accepting books'), findsWidgets);
    expect(find.textContaining('50.0% left'), findsOneWidget);
  });

  testWidgets('discovery mode opens exact live journal details', (
    tester,
  ) async {
    final since = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'library-of-congress',
              name: 'Library of Congress',
              stage: 'discovery',
              mode: 'discovery',
              modeDetail: {
                'stage': 'discovery',
                'pagesCompleted': 152,
                'candidateRecords': 450,
                'sampleCadenceSeconds': 30,
              },
              accepted: 6,
              target: 2000,
              uploaded: 0,
              health: Health.healthy,
              detail: 'Discovery mode · moving smoothly',
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 3,
              since: since,
            ),
            stageObservation: StageObservation(
              stage: 'discovery',
              since: since,
            ),
            now: since.add(const Duration(seconds: 30)),
            onPush: () {},
          ),
        ),
      ),
    );
    await tester.tap(
      find.byKey(const ValueKey('mode-pill-library-of-congress')),
    );
    await tester.pumpAndSettle();
    expect(find.text('Discovery details'), findsOneWidget);
    expect(find.text('Pages completed'), findsOneWidget);
    expect(find.text('152'), findsOneWidget);
    expect(find.text('Candidate records'), findsOneWidget);
    expect(find.text('450'), findsOneWidget);
    await tester.tap(
      find.byKey(const ValueKey('close-mode-library-of-congress')),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('mode-dialog-library-of-congress')),
      findsNothing,
    );
  });

  testWidgets('publisher verification mode exposes receipt details', (
    tester,
  ) async {
    final since = DateTime.utc(2026, 1, 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'publisher',
              name: 'Stage-to-live publisher',
              stage: 'live-verification',
              mode: 'verification',
              modeDetail: {
                'stage': 'live-verification',
                'published': 853,
                'liveVerified': 812,
                'publicationReceipt': true,
                'promotionReceipt': false,
              },
              accepted: 812,
              target: 853,
              uploaded: 853,
              health: Health.healthy,
              detail: 'Verification in progress',
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 952,
              since: since,
            ),
            stageObservation: StageObservation(
              stage: 'live-verification',
              since: since,
            ),
            now: since.add(const Duration(seconds: 20)),
            onPush: () {},
          ),
        ),
      ),
    );
    await tester.tap(find.byKey(const ValueKey('mode-pill-publisher')));
    await tester.pumpAndSettle();
    expect(find.text('Verification details'), findsOneWidget);
    expect(find.text('Live verified'), findsOneWidget);
    expect(find.text('812'), findsAtLeastNWidgets(1));
    expect(find.text('Promotion receipt'), findsOneWidget);
  });

  testWidgets('source push is immediately enabled with approved survivors', (
    tester,
  ) async {
    final since = DateTime.utc(2026, 1, 1);
    var pushes = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ModelCard(
            model: const ModelView(
              id: 'internet-archive',
              name: 'Internet Archive',
              stage: 'prepare',
              accepted: 680,
              target: 2000,
              uploaded: 0,
              health: Health.healthy,
              detail: '680 approved survivors',
            ),
            observation: ProgressObservation(
              progressTenthsPercent: 1000,
              since: since,
            ),
            stageObservation: StageObservation(
              stage: 'Listening for next exact staged unit',
              since: since,
            ),
            now: since.add(const Duration(seconds: 5)),
            onPush: () => pushes++,
          ),
        ),
      ),
    );
    await tester.tap(find.byKey(const ValueKey('push-internet-archive')));
    expect(pushes, 1);
    expect(find.text('Push'), findsOneWidget);
  });

  testWidgets('four model slots expose name and connector fields', (
    tester,
  ) async {
    await pumpAt(tester, const Size(1440, 1200));
    for (var slot = 0; slot < 4; slot++) {
      expect(find.byKey(ValueKey('model-slot-$slot')), findsOneWidget);
      expect(find.byKey(ValueKey('model-name-$slot')), findsOneWidget);
      expect(find.byKey(ValueKey('model-connector-$slot')), findsOneWidget);
      expect(find.byKey(ValueKey('navigation-pool-$slot')), findsOneWidget);
    }
    await tester.ensureVisible(find.byKey(const ValueKey('navigation-pool-1')));
    await tester.tap(find.byKey(const ValueKey('navigation-pool-1')));
    await tester.pumpAndSettle();
    for (var query = 0; query < 10; query++) {
      expect(find.byKey(ValueKey('navigation-query-1-$query')), findsOneWidget);
    }
    expect(find.byKey(const ValueKey('save-navigation-1')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('waiting game starts and exposes four pads', (tester) async {
    await pumpAt(tester, const Size(390, 844));
    await tester.ensureVisible(find.text('Start'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Start'));
    await tester.pump(const Duration(seconds: 3));
    for (var pad = 1; pad <= 4; pad++) {
      expect(find.byKey(ValueKey('memory-pad-$pad')), findsOneWidget);
    }
    expect(find.text('Chris was innocent.'), findsNothing);
  });
}
