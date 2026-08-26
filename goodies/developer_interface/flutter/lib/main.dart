import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

const _defaultEndpoint = String.fromEnvironment(
  'CONTROLLER_URL',
  defaultValue: 'http://127.0.0.1:8790',
);
const _webSweeperEndpoint = 'http://127.0.0.1:8790';
const statusRefreshInterval = Duration(seconds: 5);

void main() => runApp(const WebSweeperDeveloperApp());

class WebSweeperDeveloperApp extends StatefulWidget {
  const WebSweeperDeveloperApp({super.key});

  @override
  State<WebSweeperDeveloperApp> createState() => _WebSweeperDeveloperAppState();
}

class _WebSweeperDeveloperAppState extends State<WebSweeperDeveloperApp> {
  static const _defaultText = Color(0xffedf7ef);
  Color _textColor = _defaultText;

  @override
  void initState() {
    super.initState();
    _restoreColor();
  }

  Future<void> _restoreColor() async {
    final preferences = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _textColor = Color(
        preferences.getInt('interface_text_color') ?? _defaultText.toARGB32(),
      );
    });
  }

  Future<void> _setTextColor(Color color) async {
    setState(() => _textColor = color);
    final preferences = await SharedPreferences.getInstance();
    await preferences.setInt('interface_text_color', color.toARGB32());
  }

  @override
  Widget build(BuildContext context) {
    final base = ThemeData.dark(useMaterial3: true);
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Web Sweeper Developer Interface',
      theme: base.copyWith(
        scaffoldBackgroundColor: const Color(0xff07110d),
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xff35d07f),
          brightness: Brightness.dark,
        ),
        textTheme: base.textTheme.apply(
          bodyColor: _textColor,
          displayColor: _textColor,
        ),
        appBarTheme: AppBarTheme(
          backgroundColor: const Color(0xff0b1913),
          foregroundColor: _textColor,
          elevation: 0,
        ),
        inputDecorationTheme: const InputDecorationTheme(
          filled: true,
          fillColor: Color(0xff10251b),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.all(Radius.circular(12)),
          ),
        ),
        cardTheme: const CardThemeData(
          color: Color(0xff0d1d16),
          margin: EdgeInsets.zero,
          elevation: 0,
          shape: RoundedRectangleBorder(
            side: BorderSide(color: Color(0xff1e4733)),
            borderRadius: BorderRadius.all(Radius.circular(18)),
          ),
        ),
      ),
      home: DeveloperDashboard(
        textColor: _textColor,
        onTextColorChanged: _setTextColor,
      ),
    );
  }
}

class DeveloperDashboard extends StatefulWidget {
  const DeveloperDashboard({
    super.key,
    required this.textColor,
    required this.onTextColorChanged,
  });

  final Color textColor;
  final ValueChanged<Color> onTextColorChanged;

  @override
  State<DeveloperDashboard> createState() => _DeveloperDashboardState();
}

class RequestGenerationGate {
  int _latest = 0;

  int begin() => ++_latest;

  bool canCommit(int generation) => generation == _latest;
}

class _DeveloperDashboardState extends State<DeveloperDashboard> {
  int _sourceSlots = 4;
  int _selectedSlot = 0;
  bool _tertiary = true;
  bool _bridge = false;
  int _activeConnections = 0;
  final RequestGenerationGate _requestGate = RequestGenerationGate();
  bool get _connecting => _activeConnections > 0;
  bool _resettingUi = false;
  bool _hasLiveData = false;
  int _codexLive = 0;
  int _confirmedStaged = 0;
  int _optimizationPoints = 0;
  List<ArchivedSourceView> _archivedSources = const [];
  String _endpoint = _defaultEndpoint;
  String _token = '';
  String _connectionMessage = 'Local controller not connected';
  Timer? _progressTimer;
  Timer? _statusTimer;
  DateTime _clock = DateTime.now();
  final Map<String, ProgressObservation> _progressObservations = {};
  final Map<String, StageObservation> _stageObservations = {};
  final Set<String> _loggedProgressObservations = {};
  final Set<String> _actionsInFlight = {};
  final List<ActivityEntry> _activity = [];
  final List<ModelSlotDraft> _modelSlots = List.generate(10, (index) {
    if (index == 0) {
      return ModelSlotDraft(
        name: 'Open Library',
        connector: 'https://openlibrary.org/developers/api',
      );
    }
    if (index == 1) {
      return ModelSlotDraft(
        name: 'Open Library · Model 1 Parallel',
        connector: 'https://openlibrary.org/developers/api',
      );
    }
    return ModelSlotDraft();
  });

  List<ModelView> _models = const [
    ModelView(
      id: 'open-library',
      name: 'Open Library',
      stage: 'Awaiting controller',
      accepted: 0,
      target: 2000,
      uploaded: 0,
      health: Health.watch,
      detail: 'Connect to read live status',
    ),
    ModelView(
      id: 'library-of-congress',
      name: 'Library of Congress',
      stage: 'Awaiting controller',
      accepted: 0,
      target: 2000,
      uploaded: 0,
      health: Health.watch,
      detail: 'Connect to read live status',
    ),
    ModelView(
      id: 'publisher',
      name: 'Stage-to-live publisher',
      stage: 'Awaiting controller',
      accepted: 0,
      target: 100,
      uploaded: 0,
      health: Health.watch,
      detail: 'Connect to read live publication receipts',
    ),
  ];

  @override
  void initState() {
    super.initState();
    _syncProgressObservations(_models);
    _progressTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) _tickObservations();
    });
    _statusTimer = Timer.periodic(statusRefreshInterval, (_) {
      // Cold-start failures must heal too. Requiring prior live data here
      // trapped the interface in preview mode after a brief controller outage.
      // Always bypass caches: this timer defines UI health as current live
      // controller state, not a previously successful response.
      if (!_connecting) _connect(quiet: true, forceNetwork: true);
    });
    _restoreConnection();
  }

  @override
  void dispose() {
    _progressTimer?.cancel();
    _statusTimer?.cancel();
    super.dispose();
  }

  void _syncProgressObservations(List<ModelView> models) {
    final now = DateTime.now();
    final activeIds = models.map((model) => model.id).toSet();
    _progressObservations.removeWhere((id, _) => !activeIds.contains(id));
    _stageObservations.removeWhere((id, _) => !activeIds.contains(id));
    for (final model in models) {
      final key = model.progressTenthsPercent;
      final supplied = model.progressSince;
      final current = _progressObservations[model.id];
      if (current == null || current.progressTenthsPercent != key) {
        _progressObservations[model.id] = ProgressObservation(
          progressTenthsPercent: key,
          since: supplied ?? now,
        );
      } else if (supplied != null && supplied.isAfter(current.since)) {
        _progressObservations[model.id] = ProgressObservation(
          progressTenthsPercent: key,
          since: supplied,
        );
      }
      final stageCurrent = _stageObservations[model.id];
      final stageSupplied = model.stageSince;
      if (stageCurrent == null || stageCurrent.stage != model.stage) {
        _stageObservations[model.id] = StageObservation(
          stage: model.stage,
          since: stageSupplied ?? now,
        );
      } else if (stageSupplied != null &&
          stageSupplied.isBefore(stageCurrent.since)) {
        _stageObservations[model.id] = StageObservation(
          stage: model.stage,
          since: stageSupplied,
        );
      }
    }
  }

  void _tickObservations() {
    final now = DateTime.now();
    if (_hasLiveData) {
      for (final model in _models) {
        final observation = _progressObservations[model.id];
        if (observation == null ||
            now.difference(observation.since).inSeconds < 10) {
          continue;
        }
        final key =
            '${model.id}|${observation.progressTenthsPercent}|${observation.since}';
        if (_loggedProgressObservations.add(key)) {
          _activity.insert(
            0,
            ActivityEntry(
              at: now,
              text: '${model.name} had no counter movement for 10 seconds',
              color: model.health == Health.healthy
                  ? const Color(0xff35d07f)
                  : const Color(0xfff5d142),
            ),
          );
          if (_activity.length > 20) _activity.removeLast();
        }
      }
    }
    setState(() => _clock = now);
  }

  Future<void> _restoreConnection() async {
    final preferences = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _endpoint = preferences.getString('controller_endpoint') ?? _endpoint;
      _token = preferences.getString('controller_token') ?? '';
    });
    await _connect(quiet: true);
  }

  Future<bool> _connect({
    bool quiet = false,
    bool resetUiObservations = false,
    bool forceNetwork = false,
    bool supersedeInFlight = false,
    bool workspaceCorrectionAttempted = false,
  }) async {
    if (_connecting && !supersedeInFlight) return false;
    final requestGeneration = _requestGate.begin();
    setState(() {
      _activeConnections += 1;
      if (!quiet) _connectionMessage = 'Connecting…';
    });
    try {
      final base = _endpoint.endsWith('/')
          ? _endpoint.substring(0, _endpoint.length - 1)
          : _endpoint;
      final statusUri = Uri.parse('$base/api/status').replace(
        queryParameters: forceNetwork
            ? {'refresh': DateTime.now().microsecondsSinceEpoch.toString()}
            : null,
      );
      final response = await http
          .get(
            statusUri,
            headers: {
              if (_token.isNotEmpty) 'Authorization': 'Bearer $_token',
              if (forceNetwork) 'Cache-Control': 'no-cache, no-store',
            },
          )
          .timeout(const Duration(seconds: 8));
      if (response.statusCode != 200) {
        throw Exception('controller returned ${response.statusCode}');
      }
      final payload = jsonDecode(response.body) as Map<String, dynamic>;
      if (!_requestGate.canCommit(requestGeneration)) return false;
      final preferences = await SharedPreferences.getInstance();
      const expectedWorkspace = 'web_sweeper';
      final actualWorkspace = payload['workspace'] as String?;
      if (actualWorkspace != null && actualWorkspace != expectedWorkspace) {
        if (workspaceCorrectionAttempted) {
          throw Exception(
            'controller workspace mismatch: expected $expectedWorkspace, got $actualWorkspace',
          );
        }
        const correctedEndpoint = _webSweeperEndpoint;
        await preferences.setString('controller_endpoint', correctedEndpoint);
        if (!mounted) return false;
        setState(() {
          _endpoint = correctedEndpoint;
          _hasLiveData = false;
          _models = const [];
          _connectionMessage = 'Correcting controller workspace…';
        });
        return _connect(
          quiet: quiet,
          resetUiObservations: true,
          forceNetwork: true,
          supersedeInFlight: true,
          workspaceCorrectionAttempted: true,
        );
      }
      await preferences.setString('controller_endpoint', _endpoint);
      await preferences.setString('controller_token', _token);
      final lanes = payload['lanes'];
      final parsedModels = lanes is List
          ? lanes
                .whereType<Map>()
                .map(
                  (raw) => ModelView.fromJson(Map<String, dynamic>.from(raw)),
                )
                .toList()
          : null;
      final archived = payload['archivedSources'];
      final parsedArchived = archived is List
          ? archived
                .whereType<Map>()
                .map(
                  (raw) => ArchivedSourceView.fromJson(
                    Map<String, dynamic>.from(raw),
                  ),
                )
                .toList()
          : const <ArchivedSourceView>[];
      if (!mounted) return false;
      setState(() {
        _codexLive = (payload['codexLive'] as num?)?.toInt() ?? _codexLive;
        _confirmedStaged =
            (payload['confirmedStaged'] as num?)?.toInt() ?? _confirmedStaged;
        final optimization = payload['optimizationStandard'];
        if (optimization is Map) {
          _optimizationPoints =
              (optimization['points'] as num?)?.toInt() ?? _optimizationPoints;
        }
        if (parsedModels != null) {
          if (resetUiObservations) {
            _progressObservations.clear();
            _stageObservations.clear();
            _loggedProgressObservations.clear();
          }
          _syncProgressObservations(parsedModels);
          _models = parsedModels;
          _hasLiveData = true;
        }
        _archivedSources = parsedArchived;
        _connectionMessage = 'Connected · live controller data';
      });
      return true;
    } catch (error) {
      if (!mounted || !_requestGate.canCommit(requestGeneration)) return false;
      setState(() => _connectionMessage = 'Connection failed · $error');
      return false;
    } finally {
      if (mounted) {
        setState(() {
          if (_activeConnections > 0) _activeConnections -= 1;
        });
      }
    }
  }

  Future<void> _manualRefresh() async {
    if (_resettingUi) return;
    setState(() => _resettingUi = true);
    // Keep the last proven controller snapshot visible until a replacement
    // succeeds. A transient controller restart must never replace live cards
    // with preview lanes or zero counters.
    setState(() {
      _connectionMessage = 'Refreshing UI · loading authoritative status…';
    });
    var refreshed = await _connect(
      resetUiObservations: true,
      forceNetwork: true,
      supersedeInFlight: true,
    );
    if (!refreshed && mounted) {
      await Future<void>.delayed(const Duration(milliseconds: 250));
      refreshed = await _connect(
        quiet: true,
        resetUiObservations: true,
        forceNetwork: true,
        supersedeInFlight: true,
      );
    }
    if (!mounted) return;
    if (refreshed) {
      setState(() => _activity.clear());
    }
    final time = TimeOfDay.now().format(context);
    _notice(
      refreshed
          ? 'UI refreshed · authoritative status loaded · $time'
          : 'UI refresh · controller unavailable · $time',
    );
    setState(() => _resettingUi = false);
  }

  Future<void> _sendAction(String action, {String? laneId}) async {
    if (_models.isEmpty) {
      _notice('No configured lane is available');
      return;
    }
    final selectedName = _modelSlots[_selectedSlot].name.trim();
    final selected = laneId == null
        ? _models.firstWhere(
            (model) => model.name == selectedName,
            orElse: () => _models.first,
          )
        : _models.firstWhere(
            (model) => model.id == laneId,
            orElse: () => _models.first,
          );
    final requestedLane = laneId == 'fleet' ? 'fleet' : selected.id;
    final actionKey = '$action:$requestedLane';
    if (_actionsInFlight.contains(actionKey)) return;
    final priorModels = List<ModelView>.of(_models);
    setState(() {
      _actionsInFlight.add(actionKey);
      _models = _models.map((model) {
        final affected = requestedLane == 'fleet' || model.id == requestedLane;
        if (!affected) return model;
        return model.copyWith(
          stage: action == 'push'
              ? 'Push requested · awaiting handoff receipt'
              : action == 'clean-sweep'
              ? 'Clean sweep · stopping and relaunching'
              : 'Reset requested · stopping lane',
          health: Health.watch,
          detail: 'Controller accepted the UI transition request',
        );
      }).toList();
    });
    try {
      final base = _endpoint.endsWith('/')
          ? _endpoint.substring(0, _endpoint.length - 1)
          : _endpoint;
      final response = await http
          .post(
            Uri.parse('$base/api/action'),
            headers: {
              'Content-Type': 'application/json',
              if (_token.isNotEmpty) 'Authorization': 'Bearer $_token',
            },
            body: jsonEncode({'action': action, 'lane': requestedLane}),
          )
          .timeout(const Duration(seconds: 8));
      if (!mounted) return;
      if (response.statusCode == 202 || response.statusCode == 200) {
        final payload = jsonDecode(response.body) as Map<String, dynamic>;
        final pushedBooks = (payload['books'] as num?)?.toInt();
        _notice(
          action == 'push' && pushedBooks != null
              ? '$pushedBooks approved books handed to staging from ${selected.name}'
              : '${action[0].toUpperCase()}${action.substring(1)} request accepted for '
                    '${requestedLane == 'fleet' ? 'both Web Sweeper models' : selected.name}',
        );
        setState(() {
          if (action == 'push' && pushedBooks != null) {
            _models = _models.map((model) {
              if (model.id == selected.id) {
                return model.copyWith(
                  stage: 'Handoff accepted · next acquisition unit',
                  accepted: 0,
                  detail:
                      '$pushedBooks approved books moved to protected staging',
                );
              }
              if (model.id == 'publisher') {
                return model.copyWith(
                  stage: 'Approved handoff · preparing for staging',
                  accepted: pushedBooks,
                  target: pushedBooks,
                  health: Health.watch,
                  detail:
                      'Exact pushed batch received · publisher refresh in progress',
                );
              }
              return model;
            }).toList();
          } else if (action == 'clean-sweep') {
            _models = _models
                .map(
                  (model) => model.copyWith(
                    stage: model.id == 'publisher'
                        ? 'Clean sweep · preserving receipts'
                        : 'Initialize · clean relaunch',
                    accepted: 0,
                    target: model.id == 'publisher' ? 0 : model.target,
                    uploaded: 0,
                    health: Health.watch,
                  ),
                )
                .toList();
          }
          _syncProgressObservations(_models);
        });
        if (action == 'push') {
          await _connect(quiet: true, forceNetwork: true);
        } else if (action == 'clean-sweep') {
          await Future<void>.delayed(const Duration(seconds: 2));
          await _connect(
            quiet: true,
            resetUiObservations: true,
            forceNetwork: true,
          );
        } else {
          await _connect(
            quiet: true,
            resetUiObservations: true,
            forceNetwork: true,
          );
        }
      } else {
        setState(() => _models = priorModels);
        _notice(
          'Request not accepted · controller returned ${response.statusCode}',
        );
      }
    } catch (error) {
      if (mounted) {
        setState(() => _models = priorModels);
        _notice('Request failed · $error');
      }
    } finally {
      if (mounted) setState(() => _actionsInFlight.remove(actionKey));
    }
  }

  Future<void> _confirmCleanSweep() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        key: const ValueKey('clean-sweep-confirmation'),
        title: const Text('Clean sweep both models?'),
        content: const Text(
          'Stops both acquisition owners, removes every disposable lane-local '
          'cache, checkpoint, cursor, candidate slice, processed/rejected '
          'memory, download cache, and unfinished root, then launches clean '
          'models. Permanent receipts and shared staged/live duplicate '
          'protection are preserved.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton.icon(
            key: const ValueKey('confirm-clean-sweep-button'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            icon: const Icon(Icons.cleaning_services_rounded),
            label: const Text('Clean Sweep'),
          ),
        ],
      ),
    );
    if (confirmed == true && mounted) {
      await _sendAction('clean-sweep', laneId: 'fleet');
    }
  }

  Future<void> _confirmLaneCleanReset(ModelView model) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        key: ValueKey('clean-reset-confirmation-${model.id}'),
        title: Text('Clean reset ${model.name}?'),
        content: const Text(
          'Stops this acquisition lane, removes only its disposable local '
          'cache, checkpoints, cursors, candidate memory, download cache, and '
          'unfinished roots, then relaunches it with two workers. Shared '
          'duplicate protection, completed staging roots, and permanent live '
          'receipts remain protected.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton.icon(
            key: ValueKey('confirm-clean-reset-${model.id}'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            icon: const Icon(Icons.cleaning_services_rounded),
            label: const Text('Clean reset'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await _sendAction('clean-reset-lane', laneId: model.id);
      await Future<void>.delayed(const Duration(seconds: 2));
      await _connect(
        quiet: true,
        resetUiObservations: true,
        forceNetwork: true,
      );
    }
  }

  Future<void> _saveConfiguration() async {
    try {
      final base = _endpoint.endsWith('/')
          ? _endpoint.substring(0, _endpoint.length - 1)
          : _endpoint;
      final response = await http
          .post(
            Uri.parse('$base/api/preferences'),
            headers: {
              'Content-Type': 'application/json',
              if (_token.isNotEmpty) 'Authorization': 'Bearer $_token',
            },
            body: jsonEncode({
              'sourceSlots': _sourceSlots,
              'tertiaryEnabled': _tertiary,
              'models': [
                for (var index = 0; index < _sourceSlots; index++)
                  _modelSlots[index].toJson(index + 1),
              ],
            }),
          )
          .timeout(const Duration(seconds: 8));
      if (!mounted) return;
      if (response.statusCode == 200) {
        _notice('Model slots saved');
      } else {
        _notice('Configuration not saved · controller ${response.statusCode}');
      }
    } catch (error) {
      if (mounted) _notice('Configuration not saved · $error');
    }
  }

  Future<void> _saveNavigation(int slotIndex) async {
    final slot = _modelSlots[slotIndex];
    final selected = _models.firstWhere(
      (model) => model.name == slot.name.trim(),
      orElse: () => _models[slotIndex.clamp(0, _models.length - 1)],
    );
    try {
      final base = _endpoint.endsWith('/')
          ? _endpoint.substring(0, _endpoint.length - 1)
          : _endpoint;
      final response = await http
          .post(
            Uri.parse('$base/api/navigation'),
            headers: {
              'Content-Type': 'application/json',
              if (_token.isNotEmpty) 'Authorization': 'Bearer $_token',
            },
            body: jsonEncode({
              'lane': selected.id,
              'queries': slot.navigationQueries,
            }),
          )
          .timeout(const Duration(seconds: 8));
      if (!mounted) return;
      _notice(
        response.statusCode == 202
            ? 'Navigation pool saved for ${selected.name}'
            : 'Navigation not saved · controller ${response.statusCode}',
      );
    } catch (error) {
      if (mounted) _notice('Navigation not saved · $error');
    }
  }

  void _notice(String message) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final compactHeader = MediaQuery.sizeOf(context).width < 620;
    return Scaffold(
      appBar: AppBar(
        toolbarHeight: 72,
        titleSpacing: 14,
        title: Row(
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: Image.asset(
                'assets/sweeper-logo.png',
                width: 50,
                height: 50,
                fit: BoxFit.cover,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    'WEB SWEEPER',
                    maxLines: 1,
                    overflow: TextOverflow.fade,
                    softWrap: false,
                    style: TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 1.1,
                    ),
                  ),
                  Text(
                    'Developer Interface',
                    maxLines: 1,
                    overflow: TextOverflow.fade,
                    softWrap: false,
                    style: TextStyle(fontSize: 12, color: Color(0xff83cda4)),
                  ),
                ],
              ),
            ),
          ],
        ),
        actions: [
          if (compactHeader)
            IconButton(
              key: const ValueKey('clean-sweep-button'),
              onPressed: _connecting ? null : _confirmCleanSweep,
              tooltip: 'Clean Sweep both models',
              icon: const Icon(Icons.cleaning_services_rounded),
            )
          else
            TextButton.icon(
              key: const ValueKey('clean-sweep-button'),
              onPressed: _connecting ? null : _confirmCleanSweep,
              icon: const Icon(Icons.cleaning_services_rounded, size: 18),
              label: const Text('Clean Sweep'),
            ),
          TextButton.icon(
            key: const ValueKey('refresh-ui-button'),
            onPressed: _resettingUi ? null : _manualRefresh,
            icon: _resettingUi
                ? const SizedBox.square(
                    dimension: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.refresh_rounded, size: 19),
            label: Text(_resettingUi ? 'Refreshing…' : 'Refresh UI'),
          ),
          const SizedBox(width: 6),
        ],
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 32),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 1500),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _topArea(),
                  const SizedBox(height: 16),
                  _sectionTitle(
                    'Operating lanes',
                    'Live status, progress, and current stage',
                  ),
                  const SizedBox(height: 10),
                  LayoutBuilder(
                    builder: (context, constraints) {
                      final columns = constraints.maxWidth >= 1050
                          ? 3
                          : constraints.maxWidth >= 660
                          ? 2
                          : 1;
                      final width =
                          (constraints.maxWidth - (columns - 1) * 12) / columns;
                      return Wrap(
                        spacing: 12,
                        runSpacing: 12,
                        children: _models
                            .map(
                              (model) => SizedBox(
                                width: width,
                                child: ModelCard(
                                  model: model,
                                  observation: _progressObservations[model.id],
                                  stageObservation:
                                      _stageObservations[model.id],
                                  now: _clock,
                                  onEmergencyReset: () =>
                                      _sendAction('reset', laneId: model.id),
                                  onCleanReset: () =>
                                      _confirmLaneCleanReset(model),
                                  onPush: () =>
                                      _sendAction('push', laneId: model.id),
                                ),
                              ),
                            )
                            .toList(),
                      );
                    },
                  ),
                  const SizedBox(height: 18),
                  _sectionTitle(
                    'Success history',
                    'Completed batches, staged totals, and verified live results',
                  ),
                  const SizedBox(height: 10),
                  LayoutBuilder(
                    builder: (context, constraints) {
                      final columns = constraints.maxWidth >= 1050
                          ? 3
                          : constraints.maxWidth >= 660
                          ? 2
                          : 1;
                      final width =
                          (constraints.maxWidth - (columns - 1) * 12) / columns;
                      return Wrap(
                        spacing: 12,
                        runSpacing: 12,
                        children: [
                          ..._models.map(
                            (model) => SizedBox(
                              width: width,
                              child: _successHistoryCard(model),
                            ),
                          ),
                          ..._archivedSources.map(
                            (source) => SizedBox(
                              width: width,
                              child: _archivedSourceCard(source),
                            ),
                          ),
                        ],
                      );
                    },
                  ),
                  const SizedBox(height: 18),
                  _controls(),
                  const SizedBox(height: 18),
                  _activityLog(),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _successHistoryCard(ModelView model) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              model.name,
              style: const TextStyle(fontWeight: FontWeight.w900),
            ),
            const SizedBox(height: 9),
            if (model.successHistory.isEmpty)
              const Text(
                'No completed batch receipt yet',
                style: TextStyle(color: Color(0xff83a891), fontSize: 12),
              )
            else
              for (final item in model.successHistory.take(5))
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Row(
                    children: [
                      const Icon(
                        Icons.check_circle_rounded,
                        size: 15,
                        color: Color(0xff64dc98),
                      ),
                      const SizedBox(width: 7),
                      Text(
                        '${model.id == 'publisher' ? 'Unit' : 'Batch'} ${item.batchNumber}',
                        style: const TextStyle(fontWeight: FontWeight.w800),
                      ),
                      const Spacer(),
                      Text(
                        item.liveVerified > 0
                            ? '${item.liveVerified} live'
                            : '${item.staged} staged',
                        style: const TextStyle(
                          color: Color(0xff83cda4),
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ],
                  ),
                ),
          ],
        ),
      ),
    );
  }

  Widget _archivedSourceCard(ArchivedSourceView source) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              '${source.name} · Protected history',
              style: const TextStyle(fontWeight: FontWeight.w900),
            ),
            const SizedBox(height: 7),
            Text(
              '${source.liveVerified} live-verified · ${source.receiptCount} permanent receipts',
              style: const TextStyle(
                color: Color(0xff64dc98),
                fontWeight: FontWeight.w800,
              ),
            ),
            const SizedBox(height: 5),
            const Text(
              'Retired from active lanes · books remain protected and counted',
              style: TextStyle(color: Color(0xff83a891), fontSize: 12),
            ),
          ],
        ),
      ),
    );
  }

  Widget _topArea() {
    final currentUploaded = _models.fold<int>(
      0,
      (total, model) => total + model.uploaded,
    );
    final healthy = _models
        .where((model) => model.health == Health.healthy)
        .length;
    return LayoutBuilder(
      builder: (context, constraints) {
        final summary = Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'SYSTEM OVERVIEW',
              style: TextStyle(
                color: Color(0xff64dc98),
                fontWeight: FontWeight.w800,
              ),
            ),
            const SizedBox(height: 10),
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: [
                Metric(
                  label: 'Codex Live',
                  value: _hasLiveData ? _codexLive.toString() : '—',
                  icon: Icons.public_rounded,
                ),
                Metric(
                  label: 'Confirmed staged',
                  value: _hasLiveData ? _confirmedStaged.toString() : '—',
                  icon: Icons.inventory_2_outlined,
                ),
                Metric(
                  label: 'Current uploaded',
                  value: _hasLiveData ? currentUploaded.toString() : '—',
                  icon: Icons.cloud_upload_outlined,
                ),
                Metric(
                  label: 'Healthy lanes',
                  value: _hasLiveData ? '$healthy / ${_models.length}' : '—',
                  icon: Icons.health_and_safety_outlined,
                ),
                Metric(
                  label: 'Optimization points',
                  value: _hasLiveData && _optimizationPoints > 0
                      ? _optimizationPoints.toString()
                      : '—',
                  icon: Icons.speed_rounded,
                ),
              ],
            ),
            const SizedBox(height: 12),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(
                      Icons.verified_user_outlined,
                      color: Color(0xff64dc98),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        _hasLiveData
                            ? 'Production remains protected by one serialized writer, exact duplicate screening, and live verification.'
                            : 'Preview mode · connect the local controller to display live counts and receipts.',
                        softWrap: true,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        );

        if (constraints.maxWidth >= 980) {
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: summary),
              const SizedBox(width: 16),
              const SizedBox(width: 330, child: MemoryWaitingGame()),
            ],
          );
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            summary,
            const SizedBox(height: 14),
            const MemoryWaitingGame(),
          ],
        );
      },
    );
  }

  Widget _controls() {
    const colors = [
      Color(0xffedf7ef),
      Color(0xffffffff),
      Color(0xffb9f7d2),
      Color(0xffffe8a3),
      Color(0xffb8dcff),
      Color(0xffffc7df),
    ];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _sectionTitle(
              'Controls',
              'Set source lanes, batch sizes, uploader allowance, and manual recovery',
            ),
            const SizedBox(height: 14),
            _connectionPanel(),
            const SizedBox(height: 14),
            _modelConfiguration(),
            const SizedBox(height: 14),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                const Text(
                  'Readable text color:',
                  style: TextStyle(fontWeight: FontWeight.w700),
                ),
                for (final color in colors)
                  Semantics(
                    label:
                        'Choose text color ${color.toARGB32().toRadixString(16)}',
                    button: true,
                    child: InkWell(
                      borderRadius: BorderRadius.circular(20),
                      onTap: () => widget.onTextColorChanged(color),
                      child: Container(
                        width: 34,
                        height: 34,
                        decoration: BoxDecoration(
                          color: color,
                          shape: BoxShape.circle,
                          border: Border.all(
                            color:
                                widget.textColor.toARGB32() == color.toARGB32()
                                ? const Color(0xff35d07f)
                                : Colors.white24,
                            width:
                                widget.textColor.toARGB32() == color.toARGB32()
                                ? 3
                                : 1,
                          ),
                        ),
                        child: widget.textColor.toARGB32() == color.toARGB32()
                            ? const Icon(
                                Icons.check,
                                color: Colors.black,
                                size: 18,
                              )
                            : null,
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilterChip(
                  selected: _tertiary,
                  label: const Text('Tertiary signals'),
                  onSelected: (value) => setState(() => _tertiary = value),
                ),
                FilterChip(
                  selected: _bridge,
                  label: const Text('Bridge switch'),
                  onSelected: (value) => setState(() => _bridge = value),
                ),
              ],
            ),
            const SizedBox(height: 14),
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: [
                FilledButton.icon(
                  onPressed: _saveConfiguration,
                  icon: const Icon(Icons.save_outlined),
                  label: const Text('Save models'),
                ),
                FilledButton.tonalIcon(
                  onPressed: () => _sendAction('switch'),
                  icon: const Icon(Icons.swap_horiz_rounded),
                  label: const Text('Switch'),
                ),
                FilledButton.tonalIcon(
                  onPressed: () => _sendAction('bridge'),
                  icon: const Icon(Icons.route_outlined),
                  label: const Text('Bridge'),
                ),
                OutlinedButton.icon(
                  onPressed: () => _sendAction('reset'),
                  icon: const Icon(Icons.restart_alt_rounded),
                  label: const Text('Reset model'),
                ),
                OutlinedButton.icon(
                  onPressed: () => _sendAction('upload'),
                  icon: const Icon(Icons.publish_outlined),
                  label: const Text('Upload'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _modelConfiguration() => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Row(
        children: [
          const Expanded(
            child: Text(
              'MODEL SLOTS',
              style: TextStyle(
                color: Color(0xff64dc98),
                fontWeight: FontWeight.w800,
                fontSize: 12,
              ),
            ),
          ),
          SizedBox(
            width: 150,
            child: DropdownButtonFormField<int>(
              initialValue: _sourceSlots,
              decoration: const InputDecoration(labelText: 'Models'),
              items: [
                for (var count = 1; count <= 10; count++)
                  DropdownMenuItem(value: count, child: Text('$count')),
              ],
              onChanged: (value) {
                if (value == null) return;
                setState(() {
                  _sourceSlots = value;
                  if (_selectedSlot >= value) _selectedSlot = value - 1;
                });
              },
            ),
          ),
        ],
      ),
      const SizedBox(height: 10),
      LayoutBuilder(
        builder: (context, constraints) {
          final columns = constraints.maxWidth >= 920 ? 2 : 1;
          final width = (constraints.maxWidth - (columns - 1) * 10) / columns;
          return Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              for (var index = 0; index < _sourceSlots; index++)
                SizedBox(
                  width: width,
                  child: _modelSlot(index, _modelSlots[index]),
                ),
            ],
          );
        },
      ),
    ],
  );

  Widget _modelSlot(int index, ModelSlotDraft slot) => Container(
    key: ValueKey('model-slot-$index'),
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(
      color: const Color(0xff0a1711),
      borderRadius: BorderRadius.circular(14),
      border: Border.all(
        color: _selectedSlot == index
            ? const Color(0xff35d07f)
            : const Color(0xff1e4733),
        width: _selectedSlot == index ? 2 : 1,
      ),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'Model ${index + 1}',
                style: const TextStyle(fontWeight: FontWeight.w800),
              ),
            ),
            ChoiceChip(
              selected: _selectedSlot == index,
              label: Text(_selectedSlot == index ? 'Selected' : 'Select'),
              onSelected: (_) => setState(() => _selectedSlot = index),
            ),
          ],
        ),
        const SizedBox(height: 9),
        TextFormField(
          key: ValueKey('model-name-$index'),
          initialValue: slot.name,
          decoration: const InputDecoration(labelText: 'Model name'),
          onChanged: (value) => slot.name = value,
        ),
        const SizedBox(height: 9),
        TextFormField(
          key: ValueKey('model-connector-$index'),
          initialValue: slot.connector,
          decoration: const InputDecoration(
            labelText: 'Connector or source URL',
            hintText: 'https://source.example/api',
          ),
          keyboardType: TextInputType.url,
          onChanged: (value) => slot.connector = value,
        ),
        const SizedBox(height: 9),
        Row(
          children: [
            Expanded(
              child: _numberField(
                'Batch target',
                slot.batchTarget,
                (value) => slot.batchTarget = value.clamp(1, 100000),
                fieldKey: 'model-batch-$index',
              ),
            ),
            const SizedBox(width: 9),
            Expanded(
              child: _numberField(
                'Upload unit',
                slot.uploadTarget,
                (value) => slot.uploadTarget = value.clamp(1, 100000),
                fieldKey: 'model-upload-$index',
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        ExpansionTile(
          key: ValueKey('navigation-pool-$index'),
          tilePadding: EdgeInsets.zero,
          childrenPadding: const EdgeInsets.only(bottom: 4),
          title: const Text(
            'Navigation queries',
            style: TextStyle(fontWeight: FontWeight.w800, fontSize: 13),
          ),
          subtitle: const Text(
            '10 source-specific slots · rotate after 1h without candidate growth',
            style: TextStyle(color: Color(0xff83a891), fontSize: 10),
          ),
          children: [
            for (var queryIndex = 0; queryIndex < 10; queryIndex++) ...[
              TextFormField(
                key: ValueKey('navigation-query-$index-$queryIndex'),
                initialValue: slot.navigationQueries[queryIndex],
                decoration: InputDecoration(
                  labelText: 'Query ${queryIndex + 1}',
                ),
                onChanged: (value) =>
                    slot.navigationQueries[queryIndex] = value,
              ),
              const SizedBox(height: 7),
            ],
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton.tonalIcon(
                key: ValueKey('save-navigation-$index'),
                onPressed: () => _saveNavigation(index),
                icon: const Icon(Icons.explore_outlined),
                label: const Text('Save this source'),
              ),
            ),
          ],
        ),
      ],
    ),
  );

  Widget _connectionPanel() => Container(
    padding: const EdgeInsets.all(13),
    decoration: BoxDecoration(
      color: const Color(0xff0a1711),
      borderRadius: BorderRadius.circular(14),
      border: Border.all(color: const Color(0xff1e4733)),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Text(
          'CONTROLLER CONNECTION',
          style: TextStyle(
            color: Color(0xff64dc98),
            fontWeight: FontWeight.w800,
            fontSize: 12,
          ),
        ),
        const SizedBox(height: 9),
        LayoutBuilder(
          builder: (context, constraints) {
            final compact = constraints.maxWidth < 720;
            final endpoint = TextFormField(
              key: ValueKey('endpoint-$_endpoint'),
              initialValue: _endpoint,
              decoration: const InputDecoration(
                labelText: 'Controller URL',
                hintText: 'https://sweeper.example.org',
              ),
              keyboardType: TextInputType.url,
              onChanged: (value) => _endpoint = value.trim(),
            );
            final token = TextFormField(
              key: ValueKey('token-${_token.length}'),
              initialValue: _token,
              obscureText: true,
              enableSuggestions: false,
              autocorrect: false,
              decoration: const InputDecoration(labelText: 'Access token'),
              onChanged: (value) => _token = value,
            );
            if (compact) {
              return Column(
                children: [endpoint, const SizedBox(height: 10), token],
              );
            }
            return Row(
              children: [
                Expanded(flex: 3, child: endpoint),
                const SizedBox(width: 10),
                Expanded(flex: 2, child: token),
              ],
            );
          },
        ),
        const SizedBox(height: 9),
        Wrap(
          spacing: 10,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            FilledButton.tonalIcon(
              onPressed: _connecting ? null : _connect,
              icon: _connecting
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.link_rounded),
              label: const Text('Connect'),
            ),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 520),
              child: Text(
                _connectionMessage,
                softWrap: true,
                style: const TextStyle(fontSize: 12, color: Color(0xff83a891)),
              ),
            ),
          ],
        ),
        const SizedBox(height: 7),
        const Text(
          'Phones require a reachable HTTPS endpoint. Credentials stay on this device and are never included in public source.',
          softWrap: true,
          style: TextStyle(fontSize: 11, color: Color(0xff83a891)),
        ),
      ],
    ),
  );

  Widget _numberField(
    String label,
    int value,
    ValueChanged<int> changed, {
    String? fieldKey,
  }) {
    return TextFormField(
      key: ValueKey(fieldKey ?? '$label-$value'),
      initialValue: '$value',
      keyboardType: TextInputType.number,
      decoration: InputDecoration(labelText: label),
      onChanged: (text) {
        final parsed = int.tryParse(text);
        if (parsed != null) changed(parsed);
      },
    );
  }

  Widget _activityLog() => Card(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _sectionTitle('Activity', 'Most recent recorded messages'),
          const SizedBox(height: 10),
          if (_activity.isEmpty)
            LogRow(
              time: 'Now',
              text: _connectionMessage,
              color: _hasLiveData
                  ? const Color(0xff35d07f)
                  : const Color(0xfff5c451),
            )
          else
            for (final entry in _activity.take(10))
              LogRow(
                time: formatActivityAge(_clock.difference(entry.at)),
                text: entry.text,
                color: entry.color,
              ),
        ],
      ),
    ),
  );

  Widget _sectionTitle(String title, String subtitle) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        title,
        style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
      ),
      const SizedBox(height: 2),
      Text(
        subtitle,
        softWrap: true,
        style: const TextStyle(fontSize: 12, color: Color(0xff83a891)),
      ),
    ],
  );
}

class ModelSlotDraft {
  ModelSlotDraft({
    this.name = '',
    this.connector = '',
    this.batchTarget = 2000,
    this.uploadTarget = 100,
    List<String>? navigationQueries,
  }) : navigationQueries = List<String>.generate(
         10,
         (index) => index < (navigationQueries?.length ?? 0)
             ? navigationQueries![index]
             : '',
       );

  String name;
  String connector;
  int batchTarget;
  int uploadTarget;
  final List<String> navigationQueries;

  Map<String, dynamic> toJson(int slot) => {
    'slot': slot,
    'name': name.trim(),
    'connector': connector.trim(),
    'batchTarget': batchTarget,
    'uploadTarget': uploadTarget,
    'navigationQueries': navigationQueries,
  };
}

class ActivityEntry {
  const ActivityEntry({
    required this.at,
    required this.text,
    required this.color,
  });

  final DateTime at;
  final String text;
  final Color color;
}

enum Health { healthy, watch, stuck, failed }

class ArchivedSourceView {
  const ArchivedSourceView({
    required this.name,
    required this.liveVerified,
    required this.receiptCount,
  });

  final String name;
  final int liveVerified;
  final int receiptCount;

  factory ArchivedSourceView.fromJson(Map<String, dynamic> json) {
    return ArchivedSourceView(
      name: json['name'] as String? ?? 'Archived source',
      liveVerified: (json['liveVerified'] as num?)?.toInt() ?? 0,
      receiptCount: (json['receiptCount'] as num?)?.toInt() ?? 0,
    );
  }
}

class ModelView {
  const ModelView({
    required this.id,
    required this.name,
    required this.stage,
    required this.accepted,
    this.candidateCount = 0,
    this.candidateTarget = 0,
    required this.target,
    required this.uploaded,
    required this.health,
    required this.detail,
    this.mode = 'discovery',
    this.modeDetail = const {},
    this.progressSince,
    this.stageSince,
    this.batchNumber = 0,
    this.successHistory = const [],
    this.exhaustedSource = false,
    this.acceptanceRate,
  });
  final String id;
  final String name;
  final String stage;
  final int accepted;
  final int candidateCount;
  final int candidateTarget;
  final int target;
  final int uploaded;
  final Health health;
  final String detail;
  final String mode;
  final Map<String, dynamic> modeDetail;
  final DateTime? progressSince;
  final DateTime? stageSince;
  final int batchNumber;
  final List<SuccessRecord> successHistory;
  final bool exhaustedSource;
  final double? acceptanceRate;

  double get progress =>
      target == 0 ? 0.0 : (accepted / target).clamp(0.0, 1.0);

  int get progressTenthsPercent => (progress * 1000).round();

  ModelView copyWith({
    String? stage,
    int? accepted,
    int? candidateCount,
    int? candidateTarget,
    int? target,
    int? uploaded,
    Health? health,
    String? detail,
  }) => ModelView(
    id: id,
    name: name,
    stage: stage ?? this.stage,
    accepted: accepted ?? this.accepted,
    candidateCount: candidateCount ?? this.candidateCount,
    candidateTarget: candidateTarget ?? this.candidateTarget,
    target: target ?? this.target,
    uploaded: uploaded ?? this.uploaded,
    health: health ?? this.health,
    detail: detail ?? this.detail,
    mode: mode,
    modeDetail: modeDetail,
    progressSince: progressSince,
    stageSince: stageSince,
    batchNumber: batchNumber,
    successHistory: successHistory,
    exhaustedSource: exhaustedSource,
    acceptanceRate: acceptanceRate,
  );

  factory ModelView.fromJson(Map<String, dynamic> json) {
    final healthName = (json['health'] as String? ?? 'watch').toLowerCase();
    return ModelView(
      id: json['id'] as String? ?? 'unnamed-lane',
      name: json['name'] as String? ?? 'Unnamed lane',
      stage: json['stage'] as String? ?? 'unknown',
      accepted: (json['accepted'] as num?)?.toInt() ?? 0,
      candidateCount:
          (json['candidateCount'] as num?)?.toInt() ??
          ((json['modeDetail'] is Map
                      ? (json['modeDetail'] as Map)['candidateCount']
                      : null)
                  as num?)
              ?.toInt() ??
          0,
      candidateTarget:
          (json['candidateTarget'] as num?)?.toInt() ??
          ((json['modeDetail'] is Map
                      ? (json['modeDetail'] as Map)['candidateTarget']
                      : null)
                  as num?)
              ?.toInt() ??
          0,
      target: (json['target'] as num?)?.toInt() ?? 0,
      uploaded: (json['uploaded'] as num?)?.toInt() ?? 0,
      health: Health.values.firstWhere(
        (value) => value.name == healthName,
        orElse: () => Health.watch,
      ),
      detail: json['detail'] as String? ?? 'No current detail',
      mode:
          json['mode'] as String? ??
          ((json['id'] as String? ?? '') == 'publisher'
              ? 'verification'
              : 'discovery'),
      modeDetail: json['modeDetail'] is Map
          ? Map<String, dynamic>.from(json['modeDetail'] as Map)
          : const {},
      progressSince: DateTime.tryParse(json['progressSince'] as String? ?? ''),
      stageSince: DateTime.tryParse(json['stageSince'] as String? ?? ''),
      batchNumber: (json['batchNumber'] as num?)?.toInt() ?? 0,
      successHistory: (json['successHistory'] as List? ?? const [])
          .whereType<Map>()
          .map(
            (value) => SuccessRecord.fromJson(Map<String, dynamic>.from(value)),
          )
          .toList(),
      exhaustedSource: json['exhaustedSource'] == true,
      acceptanceRate: (json['acceptanceRate'] as num?)?.toDouble(),
    );
  }
}

class SuccessRecord {
  const SuccessRecord({
    required this.batchNumber,
    required this.staged,
    required this.published,
    required this.liveVerified,
    required this.status,
  });

  final int batchNumber;
  final int staged;
  final int published;
  final int liveVerified;
  final String status;

  factory SuccessRecord.fromJson(Map<String, dynamic> json) => SuccessRecord(
    batchNumber: (json['batchNumber'] as num?)?.toInt() ?? 0,
    staged: (json['staged'] as num?)?.toInt() ?? 0,
    published: (json['published'] as num?)?.toInt() ?? 0,
    liveVerified: (json['liveVerified'] as num?)?.toInt() ?? 0,
    status: json['status'] as String? ?? '',
  );
}

class ProgressObservation {
  const ProgressObservation({
    required this.progressTenthsPercent,
    required this.since,
  });

  final int progressTenthsPercent;
  final DateTime since;
}

class StageObservation {
  const StageObservation({required this.stage, required this.since});

  final String stage;
  final DateTime since;
}

class GatePosition {
  const GatePosition(this.current, this.total);

  final int current;
  final int total;
}

String pipelineStageLabel(ModelView model, GatePosition gate) {
  final stages = pipelineStagesFor(model);
  return stages[(gate.current - 1).clamp(0, stages.length - 1)];
}

List<String> pipelineStagesFor(ModelView model) {
  final sourceStages = [
    'Initialize',
    'Discover / acquire',
    'Validate',
    'Staging upload',
    'Staging verification',
    'Complete',
  ];
  final publisherStages = [
    'Queue / preflight',
    'Staging upload',
    'Live duplicate delta',
    'Dedup / prepare',
    'Production storage upload',
    'Publish',
    'Live verification',
    'Complete',
  ];
  return model.id == 'publisher' ? publisherStages : sourceStages;
}

List<String> pipelineSubstagesFor(ModelView model, int gateNumber) {
  final source = <List<String>>[
    [
      'Acquire lane lock',
      'Verify single owner',
      'Check 1 GiB capacity floor',
      'Restore checkpoint and journals',
      'Reconcile accepted membership',
      'Load shared duplicate protection',
      'Open source frontier',
    ],
    [
      'Refresh live Gate 0 index',
      'Select source collection',
      'Scan discovery page',
      'Parse work identity',
      'Screen live duplicates',
      'Verify rights',
      'Verify Christian relevance and English',
      'Retrieve complete text',
      'Extract and convert',
      'Accepting books',
    ],
    [
      'Freeze candidate membership',
      'Check structure and word floor',
      'Check body language',
      'Review whole-work boundaries',
      'Run global duplicate check',
      'Run substantive review',
      'Write independent attestation',
    ],
    [
      'Freeze exact staging membership',
      'Recheck capacity',
      'Upload staging objects',
      'Write staging receipt',
      'Advance upload checkpoint',
    ],
    [
      'Verify staged membership',
      'Verify catalog and manuscript hashes',
      'Verify staged readability',
      'Release exact unit to publisher queue',
    ],
    [
      'Write unit completion receipt',
      'Preserve accepted history',
      'Advance source cursor',
      'Open next protected unit',
    ],
  ];
  final publisher = <List<String>>[
    [
      'Discover queued unit',
      'Verify frozen membership',
      'Verify validation attestation',
      'Acquire serialized writer lease',
    ],
    [
      'Read staging receipt',
      'Verify exact staged membership',
      'Bind staged hashes',
    ],
    [
      'Refresh complete live index',
      'Compare source identifiers',
      'Compare work-family identities',
      'Compare content identities',
    ],
    [
      'Remove new overlaps',
      'Freeze publishable membership',
      'Bind catalog hash',
      'Bind manuscript-set hash',
    ],
    [
      'Upload manuscript objects',
      'Upload cover objects',
      'Verify production storage objects',
    ],
    [
      'Write Firestore records',
      'Update search documents',
      'Assign Living Codex rooms',
      'Confirm publication count',
    ],
    [
      'Verify Firestore',
      'Verify storage',
      'Verify search',
      'Verify room assignment',
      'Verify reader access',
    ],
    [
      'Write permanent live receipt',
      'Record success history',
      'Release writer lease',
      'Clear active card to 0 / 0',
    ],
  ];
  final groups = model.id == 'publisher' ? publisher : source;
  return groups[(gateNumber - 1).clamp(0, groups.length - 1)];
}

int activeSubstageFor(ModelView model, int gateNumber, List<String> substages) {
  final stage = model.stage.toLowerCase().replaceAll('_', '-');
  if (model.id == 'publisher' &&
      gateNumber == 8 &&
      (stage.contains('listening for next') ||
          stage.contains('ready for next') ||
          stage == 'complete')) {
    return substages.length - 1;
  }
  if (model.id != 'publisher' &&
      gateNumber == 3 &&
      stage.contains('bounded-frontier-complete')) {
    return 0;
  }
  if (model.id != 'publisher' && gateNumber == 2) {
    if (stage.contains('prepare') &&
        ((model.modeDetail['acceptedJournalCount'] as num?)?.toInt() ?? 0) >
            0) {
      return substages.length - 1;
    }
    if (stage.contains('rotate') || stage.contains('collection')) return 1;
  }
  final measuredCurrent = (model.modeDetail['gateProgressCurrent'] as num?)
      ?.toInt();
  final measuredTarget = (model.modeDetail['gateProgressTarget'] as num?)
      ?.toInt();
  if (measuredCurrent != null && measuredTarget != null && measuredTarget > 0) {
    return ((measuredCurrent / measuredTarget) * substages.length)
        .floor()
        .clamp(0, substages.length - 1);
  }
  final semanticMarkers = <String>[
    'lock',
    'capacity',
    'checkpoint',
    'live',
    'frontier',
    'discover',
    'identity',
    'rights',
    'retrieve',
    'convert',
    'structure',
    'language',
    'whole-work',
    'duplicate',
    'attestation',
    'membership',
    'upload',
    'receipt',
    'hash',
    'readability',
    'firestore',
    'search',
    'room',
    'reader',
  ];
  for (var index = substages.length - 1; index >= 0; index--) {
    final label = substages[index].toLowerCase();
    if (semanticMarkers.any(
      (marker) => stage.contains(marker) && label.contains(marker),
    )) {
      return index;
    }
  }
  return 0;
}

double? authoritativeSubstageProgress(ModelView model) {
  final current = (model.modeDetail['substageProgressCurrent'] as num?)
      ?.toInt();
  final target = (model.modeDetail['substageProgressTarget'] as num?)?.toInt();
  if (current == null || target == null || target <= 0) return null;
  return (current / target).clamp(0.0, 1.0);
}

String discoveryAcquisitionFocus(ModelView model) {
  final signal = '${model.stage} ${model.mode}'.toLowerCase();
  if (signal.contains('acquir') ||
      signal.contains('acquis') ||
      signal.contains('download') ||
      signal.contains('fetch') ||
      signal.contains('import')) {
    return 'acquire';
  }
  return 'discover';
}

GatePosition gatePositionFor(ModelView model) {
  final stage = model.stage.toLowerCase().replaceAll('_', '-');
  if (model.id == 'publisher') {
    if (stage.contains('approved handoff') ||
        stage.contains('preparing for staging') ||
        stage.contains('staged · waiting')) {
      return const GatePosition(1, 8);
    }
    if (stage.contains('ready for next') ||
        stage.contains('listening for next') ||
        stage.contains('queue-advance') ||
        stage.contains('final-live-recount') ||
        stage.contains('receipt') ||
        stage == 'complete') {
      return const GatePosition(8, 8);
    }
    if (stage.contains('live-verification') ||
        stage.contains('verification') ||
        stage.contains('verify')) {
      return const GatePosition(7, 8);
    }
    if (stage.contains('firestore') ||
        stage.contains('publication-complete') ||
        stage.contains('publish')) {
      return const GatePosition(6, 8);
    }
    if (stage.contains('staging-upload')) {
      return const GatePosition(2, 8);
    }
    if (stage.contains('storage-upload') || stage.contains('upload')) {
      return const GatePosition(5, 8);
    }
    if (stage.contains('room-allocation') ||
        stage.contains('duplicate') ||
        stage.contains('dedup')) {
      return const GatePosition(4, 8);
    }
    if (stage.contains('fresh-live-delta') || stage.contains('live-delta')) {
      return const GatePosition(3, 8);
    }
    return const GatePosition(1, 8);
  }

  if (stage.contains('batch-complete') ||
      stage.contains('campaign-complete') ||
      stage.contains('rollover') ||
      stage == 'complete') {
    return const GatePosition(6, 6);
  }
  if (stage.contains('staging-verification') ||
      stage.contains('verification') ||
      stage.contains('verify')) {
    return const GatePosition(5, 6);
  }
  if (stage.contains('staging-upload') ||
      stage.contains('storage-upload') ||
      stage.contains('firestore') ||
      stage.contains('reconcile')) {
    return const GatePosition(4, 6);
  }
  if (stage.contains('validat') || stage.contains('review')) {
    return const GatePosition(3, 6);
  }
  if (stage.contains('bounded-frontier-complete') ||
      stage.contains('candidate-preflight')) {
    return const GatePosition(3, 6);
  }
  if (stage.contains('prepare') ||
      stage.contains('fresh-live-export') ||
      stage.contains('positive-remainder') ||
      stage.contains('rotate-source') ||
      stage.contains('diminished-source') ||
      stage.contains('upstream-backoff') ||
      stage.contains('discover') ||
      stage.contains('acquir') ||
      stage.contains('acquis') ||
      stage.contains('import')) {
    return const GatePosition(2, 6);
  }
  return const GatePosition(1, 6);
}

bool isTerminalModel(ModelView model) {
  final stage = model.stage.toLowerCase().replaceAll('_', '-');
  return model.mode.toLowerCase() == 'complete' ||
      stage.contains('campaign-complete') ||
      stage.contains('batch-complete') ||
      stage.contains('source-exhausted');
}

bool isWaitingModel(ModelView model) => model.mode.toLowerCase() == 'queued';

String candidateCounterLabel(ModelView model) {
  final suffix = model.candidateTarget > 0 ? ' / ${model.candidateTarget}' : '';
  return 'Candidates screened: ${model.candidateCount}$suffix';
}

String? publisherCampaignLabel(ModelView model) {
  final verified = (model.modeDetail['campaignLiveVerified'] as num?)?.toInt();
  final books = (model.modeDetail['campaignBooksTotal'] as num?)?.toInt();
  final completed = (model.modeDetail['campaignBatchesCompleted'] as num?)
      ?.toInt();
  final batches = (model.modeDetail['campaignBatchesTotal'] as num?)?.toInt();
  if (verified == null ||
      books == null ||
      completed == null ||
      batches == null) {
    return null;
  }
  return 'Campaign live: $verified / $books · Batches: $completed / $batches';
}

class ModelCard extends StatelessWidget {
  const ModelCard({
    super.key,
    required this.model,
    required this.observation,
    required this.stageObservation,
    required this.now,
    this.onEmergencyReset,
    this.onCleanReset,
    required this.onPush,
  });
  final ModelView model;
  final ProgressObservation? observation;
  final StageObservation? stageObservation;
  final DateTime now;
  final VoidCallback? onEmergencyReset;
  final VoidCallback? onCleanReset;
  final VoidCallback onPush;

  Color get color => switch (model.health) {
    Health.healthy => const Color(0xff35d07f),
    Health.watch => const Color(0xfff5d142),
    Health.stuck => const Color(0xffff8a3d),
    Health.failed => const Color(0xffff4e5b),
  };

  String get modeLabel {
    if (isTerminalModel(model)) return 'Complete';
    if (isWaitingModel(model)) return 'Queued';
    if (model.mode.toLowerCase() == 'uploading') return 'Uploading';
    if (model.mode.toLowerCase() == 'acquisition') return 'Acquisition';
    if (model.id == 'publisher') return 'Verification';
    return 'Discovery';
  }

  Color _modeColor(Duration unchangedFor) {
    if (isTerminalModel(model) || isWaitingModel(model)) {
      return const Color(0xff35d07f);
    }
    if (model.mode.toLowerCase() == 'uploading') {
      return const Color(0xff35d07f);
    }
    if (model.health == Health.failed || unchangedFor.inSeconds >= 300) {
      return const Color(0xffff4e5b);
    }
    if (unchangedFor.inSeconds < 60) {
      return const Color(0xff35d07f);
    }
    const yellow = Color(0xfff5d142);
    const orange = Color(0xffff8a3d);
    if (unchangedFor.inSeconds < 180) return yellow;
    return orange;
  }

  String _modeButtonLabel(Duration unchangedFor) {
    if (isTerminalModel(model)) return 'Complete';
    if (isWaitingModel(model)) return 'Queued';
    if (model.mode.toLowerCase() == 'uploading') return 'Uploading';
    final age = formatDuration(unchangedFor);
    if (unchangedFor.inSeconds >= 300) return '$modeLabel · stuck $age';
    return '$modeLabel · $age';
  }

  String _detailLabel(String key) => switch (key) {
    'stage' => 'Exact gate',
    'accepted' => 'Accepted',
    'target' => 'Target',
    'discovered' => 'Discovered',
    'prefiltered' => 'Prefiltered',
    'pagesCompleted' => 'Pages completed',
    'candidateRecords' => 'Candidate records',
    'newlyCompletedPages' => 'Pages in latest sample',
    'recentQueries' => 'Queries advancing',
    'activeQuery' => 'Current query',
    'activePage' => 'Current page',
    'lastCompletedQuery' => 'Last completed query',
    'lastCompletedPage' => 'Last completed page',
    'gateProgressLabel' => 'Loading meter',
    'gateProgressCurrent' => 'Loading current',
    'gateProgressTarget' => 'Loading target',
    'discoveryFrontier' => 'Discovery frontier',
    'candidateOffset' => 'Candidate offset',
    'prepared' => 'Prepared',
    'duplicatesRemoved' => 'Duplicates removed',
    'uploaded' => 'Uploaded',
    'uploadTarget' => 'Upload target',
    'published' => 'Published',
    'liveVerified' => 'Live verified',
    'queueReady' => 'Queue ready',
    'queueParked' => 'Queue parked',
    'queuePreflight' => 'Queue preflight',
    'publicationReceipt' => 'Publication receipt',
    'promotionReceipt' => 'Promotion receipt',
    'completionState' => 'Completed state',
    'writerSerialized' => 'Serialized writer',
    'journalUpdatedAt' => 'Journal updated',
    'sampledAt' => 'Detail sampled',
    'sampleCadenceSeconds' => 'Sample cadence (seconds)',
    'checkpointUpdatedAt' => 'Checkpoint updated',
    'uploadUpdatedAt' => 'Upload updated',
    'unitUpdatedAt' => 'Unit updated',
    'watcherCheckedAt' => 'Watcher checked',
    'journalBytes' => 'Journal bytes',
    'currentRoot' => 'Current root',
    'batchNumber' => 'Current batch',
    'signalState' => 'Progress signal',
    'unchangedFor' => 'Unchanged for',
    _ => key,
  };

  void _showModeDetails(BuildContext context, Duration unchangedFor) {
    final entries = <MapEntry<String, dynamic>>[
      MapEntry(
        'signalState',
        unchangedFor.inSeconds >= 300 ? 'Stuck' : 'Moving / observed',
      ),
      MapEntry('unchangedFor', formatDuration(unchangedFor)),
      ...model.modeDetail.entries.where(
        (entry) => entry.key != 'mode' && entry.key != 'batchQueue',
      ),
    ];
    final modeColor = _modeColor(unchangedFor);
    showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        key: ValueKey('mode-dialog-${model.id}'),
        titlePadding: const EdgeInsets.fromLTRB(20, 14, 8, 4),
        title: Row(
          children: [
            Icon(
              model.mode == 'uploading'
                  ? Icons.cloud_upload_outlined
                  : model.id == 'publisher'
                  ? Icons.verified_outlined
                  : Icons.travel_explore_rounded,
              color: modeColor,
            ),
            const SizedBox(width: 9),
            Expanded(child: Text('$modeLabel details')),
            IconButton(
              key: ValueKey('close-mode-${model.id}'),
              tooltip: 'Close details',
              onPressed: () => Navigator.of(dialogContext).pop(),
              icon: const Icon(Icons.close_rounded),
            ),
          ],
        ),
        content: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                for (final entry in entries)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 5),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(
                          flex: 4,
                          child: Text(
                            _detailLabel(entry.key),
                            style: const TextStyle(color: Color(0xff83a891)),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          flex: 5,
                          child: Text(
                            '${entry.value}',
                            key: ValueKey(
                              'mode-detail-${model.id}-${entry.key}',
                            ),
                            textAlign: TextAlign.end,
                            softWrap: true,
                            style: const TextStyle(fontWeight: FontWeight.w700),
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _publisherBatchRow(Map<String, dynamic> batch, int index) {
    final status = '${batch['status'] ?? 'Queued'}';
    final statusLower = status.toLowerCase();
    final statusColor = statusLower.contains('completed')
        ? const Color(0xff35d07f)
        : statusLower.contains('block')
        ? const Color(0xffff8a3d)
        : (batch['current'] == true)
        ? const Color(0xff4cc9f0)
        : const Color(0xffffd166);
    final books = (batch['books'] as num?)?.toInt() ?? 0;
    final batchNumber = (batch['batchNumber'] as num?)?.toInt() ?? 0;
    return Container(
      key: ValueKey('publisher-queued-batch-$index'),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
      decoration: BoxDecoration(
        color: statusColor.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(9),
        border: Border.all(color: statusColor.withValues(alpha: 0.45)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.inventory_2_outlined, size: 16, color: statusColor),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${batchNumber > 0 ? 'Unit $batchNumber · ' : ''}$books books',
                  style: const TextStyle(fontWeight: FontWeight.w900),
                ),
                const SizedBox(height: 2),
                Text(
                  status,
                  style: TextStyle(
                    color: statusColor,
                    fontSize: 11,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                Text(
                  '${batch['name'] ?? 'staged batch'}',
                  softWrap: true,
                  style: const TextStyle(
                    color: Color(0xff83a891),
                    fontSize: 10,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _substagePanel(int gateNumber) {
    final substages = pipelineSubstagesFor(model, gateNumber);
    final active = activeSubstageFor(model, gateNumber, substages);
    final progress = authoritativeSubstageProgress(model);
    final substageComplete = progress != null && progress >= 1.0;
    final liveLabel =
        '${model.modeDetail['substageProgressLabel'] ?? substages[active]}';
    return Container(
      key: ValueKey('substage-panel-${model.id}-$gateNumber'),
      margin: const EdgeInsets.only(top: 10),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: const Color(0xff071b12),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withValues(alpha: 0.45)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (!substageComplete)
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Substage ${active + 1}/${substages.length} · $liveLabel',
                    key: ValueKey('substage-label-${model.id}'),
                    style: const TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                ),
                Text(
                  progress == null
                      ? '0.0% measured'
                      : '${((1 - progress) * 100).toStringAsFixed(1)}% left',
                  key: ValueKey('substage-percent-${model.id}'),
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w900,
                    color: color,
                  ),
                ),
              ],
            ),
          if (!substageComplete) const SizedBox(height: 6),
          if (!substageComplete)
            LinearProgressIndicator(
              key: ValueKey('substage-meter-${model.id}'),
              value: progress ?? 0.0,
              minHeight: 6,
              borderRadius: BorderRadius.circular(8),
              backgroundColor: const Color(0xff173426),
              color: color,
            ),
          const SizedBox(height: 8),
          for (var index = 0; index < substages.length; index++)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child: Row(
                children: [
                  Icon(
                    index < active
                        ? Icons.check_circle_rounded
                        : index == active
                        ? Icons.play_circle_fill_rounded
                        : Icons.circle_outlined,
                    size: 14,
                    color: index <= active ? color : const Color(0xff567563),
                  ),
                  const SizedBox(width: 7),
                  Expanded(
                    child: Text(
                      substages[index],
                      key: ValueKey(
                        'substage-${model.id}-$gateNumber-${index + 1}',
                      ),
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: index == active
                            ? FontWeight.w900
                            : FontWeight.w500,
                        color: index == active
                            ? color
                            : const Color(0xffa9c8b5),
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  void _showExactStage(BuildContext context, GatePosition gate) {
    final stages = pipelineStagesFor(model);
    final discoveryFocus = discoveryAcquisitionFocus(model);
    showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        key: ValueKey('exact-stage-dialog-${model.id}'),
        title: Text('Exact stage · ${model.name}'),
        content: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 460),
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  'Live controller stage: ${model.stage}',
                  key: ValueKey('exact-stage-raw-${model.id}'),
                  style: const TextStyle(color: Color(0xff83a891)),
                ),
                const SizedBox(height: 12),
                for (var index = 0; index < stages.length; index++)
                  Container(
                    key: ValueKey('exact-stage-${model.id}-${index + 1}'),
                    margin: const EdgeInsets.only(bottom: 7),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 10,
                    ),
                    decoration: BoxDecoration(
                      color: index + 1 == gate.current
                          ? color.withValues(alpha: 0.18)
                          : const Color(0xff10271c),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(
                        color: index + 1 == gate.current
                            ? color
                            : const Color(0xff254c38),
                      ),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Row(
                          children: [
                            Icon(
                              index + 1 == gate.current
                                  ? Icons.play_arrow_rounded
                                  : index + 1 < gate.current
                                  ? Icons.check_rounded
                                  : Icons.circle_outlined,
                              size: 18,
                              color: index + 1 <= gate.current
                                  ? color
                                  : const Color(0xff83a891),
                            ),
                            const SizedBox(width: 9),
                            Expanded(
                              child: index == 1 && model.id != 'publisher'
                                  ? Wrap(
                                      crossAxisAlignment:
                                          WrapCrossAlignment.center,
                                      children: [
                                        const Text('2. '),
                                        Text(
                                          'Discover',
                                          key: ValueKey(
                                            'exact-stage-${model.id}-discover',
                                          ),
                                          style: TextStyle(
                                            fontWeight:
                                                discoveryFocus == 'discover'
                                                ? FontWeight.w900
                                                : FontWeight.w600,
                                            color: discoveryFocus == 'discover'
                                                ? color
                                                : const Color(0xff83a891),
                                          ),
                                        ),
                                        const Text(' & '),
                                        Text(
                                          'Acquire',
                                          key: ValueKey(
                                            'exact-stage-${model.id}-acquire',
                                          ),
                                          style: TextStyle(
                                            fontWeight:
                                                discoveryFocus == 'acquire'
                                                ? FontWeight.w900
                                                : FontWeight.w600,
                                            color: discoveryFocus == 'acquire'
                                                ? color
                                                : const Color(0xff83a891),
                                          ),
                                        ),
                                      ],
                                    )
                                  : Text(
                                      '${index + 1}. ${stages[index]}',
                                      style: TextStyle(
                                        fontWeight: index + 1 == gate.current
                                            ? FontWeight.w900
                                            : FontWeight.w600,
                                        color: index + 1 == gate.current
                                            ? color
                                            : null,
                                      ),
                                    ),
                            ),
                            if (index + 1 == gate.current)
                              const Text(
                                'CURRENT',
                                style: TextStyle(
                                  fontSize: 10,
                                  fontWeight: FontWeight.w900,
                                ),
                              ),
                          ],
                        ),
                        if (index + 1 == gate.current)
                          _substagePanel(index + 1),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final progress = model.progress;
    final heldFor = observation == null
        ? Duration.zero
        : now.difference(observation!.since);
    final stageHeldFor = stageObservation == null
        ? Duration.zero
        : now.difference(stageObservation!.since);
    final queuedBatches = model.modeDetail['batchQueue'] is List
        ? model.modeDetail['batchQueue'] as List
        : const [];
    final publisherIdle =
        model.id == 'publisher' && model.target == 0 && queuedBatches.isEmpty;
    if (publisherIdle) {
      return Card(
        key: const ValueKey('publisher-idle-card'),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  const Icon(
                    Icons.cloud_queue_rounded,
                    color: Color(0xff83a891),
                    size: 18,
                  ),
                  const SizedBox(width: 9),
                  Expanded(
                    child: Text(
                      model.name,
                      style: const TextStyle(
                        fontWeight: FontWeight.w800,
                        fontSize: 15,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 34),
              const Icon(
                Icons.hourglass_empty_rounded,
                color: Color(0xff83cda4),
                size: 30,
              ),
              const SizedBox(height: 12),
              const Text(
                'Waiting for next staged batch',
                key: ValueKey('publisher-idle-label'),
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: Color(0xffa9e2bf),
                  fontSize: 17,
                  fontWeight: FontWeight.w900,
                ),
              ),
              const SizedBox(height: 34),
            ],
          ),
        ),
      );
    }
    final normalizedStage = model.stage.toLowerCase().replaceAll('_', '-');
    final sourceReadyToStage =
        model.id != 'publisher' &&
        model.accepted > 0 &&
        (normalizedStage.contains('validation-complete') ||
            normalizedStage.contains('acquisition-complete'));
    final capacityProtected = normalizedStage.contains('capacity-protected');
    final adapterActive = model.modeDetail['adapterActive'] == true;
    final uiStuck =
        !isTerminalModel(model) &&
        !isWaitingModel(model) &&
        !sourceReadyToStage &&
        !capacityProtected &&
        !adapterActive &&
        heldFor.inMinutes >= 5;
    final uiActive =
        !publisherIdle &&
        !uiStuck &&
        model.health != Health.failed &&
        (adapterActive || heldFor.inSeconds <= 60);
    final activityColor = capacityProtected
        ? const Color(0xffffd166)
        : sourceReadyToStage
        ? const Color(0xff35d07f)
        : uiStuck
        ? const Color(0xffff4d67)
        : uiActive
        ? const Color(0xff35d07f)
        : publisherIdle
        ? const Color(0xff83a891)
        : const Color(0xfff5d142);
    final activityLabel = capacityProtected
        ? 'Accepted books protected'
        : sourceReadyToStage
        ? 'Ready to stage'
        : uiStuck
        ? 'Stuck'
        : uiActive
        ? adapterActive
              ? 'Adapter moving'
              : 'UI active'
        : publisherIdle
        ? 'UI idle'
        : 'No current activity';
    final gate = gatePositionFor(model);
    final pipelineLabel = pipelineStageLabel(model, gate);
    final gateSeconds = max(0, stageHeldFor.inSeconds);
    final pushReady = model.id != 'publisher' && model.accepted > 0;
    final modeColor = _modeColor(heldFor);
    final pagesCompleted = (model.modeDetail['pagesCompleted'] as num?)
        ?.toInt();
    final candidateRecords = (model.modeDetail['candidateRecords'] as num?)
        ?.toInt();
    final discoverySummary = model.mode == 'discovery' && pagesCompleted != null
        ? '$pagesCompleted pages · ${candidateRecords ?? 0} candidates · '
              '${model.accepted} survivors carried forward'
        : null;
    final completionState = '${model.modeDetail['completionState'] ?? ''}';
    final batchQueue = model.modeDetail['batchQueue'] is List
        ? (model.modeDetail['batchQueue'] as List)
              .whereType<Map>()
              .map((item) => Map<String, dynamic>.from(item))
              .toList()
        : const <Map<String, dynamic>>[];
    final gateProgressLabel =
        '${model.modeDetail['gateProgressLabel'] ?? 'Gate loading'}';
    final gateProgressCurrent =
        (model.modeDetail['gateProgressCurrent'] as num?)?.toInt();
    final gateProgressTarget = (model.modeDetail['gateProgressTarget'] as num?)
        ?.toInt();
    final gateProgress =
        gateProgressCurrent != null &&
            gateProgressTarget != null &&
            gateProgressTarget > 0
        ? (gateProgressCurrent / gateProgressTarget).clamp(0.0, 1.0)
        : null;
    final activeSubstages = pipelineSubstagesFor(model, gate.current);
    final activeSubstage = activeSubstageFor(
      model,
      gate.current,
      activeSubstages,
    );
    final substageProgress = authoritativeSubstageProgress(model);
    final substageComplete =
        substageProgress != null && substageProgress >= 1.0;
    final activityPulse = ((now.second % 10) + 1) / 10;
    final substageProgressLabel =
        '${model.modeDetail['substageProgressLabel'] ?? activeSubstages[activeSubstage]}';
    final overallProgress =
        ((gate.current - 1 + (substageProgress ?? 0)) / gate.total).clamp(
          0.0,
          1.0,
        );
    final completionLabel = switch (completionState) {
      'published' => 'Published',
      'staged' => 'Staged',
      _ => '',
    };
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(15),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 10,
                  height: 10,
                  margin: const EdgeInsets.only(top: 5),
                  decoration: BoxDecoration(
                    color: color,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 9),
                Expanded(
                  child: Text(
                    model.name,
                    softWrap: true,
                    style: const TextStyle(
                      fontWeight: FontWeight.w800,
                      fontSize: 15,
                    ),
                  ),
                ),
                if (model.batchNumber > 0) ...[
                  const SizedBox(width: 6),
                  Container(
                    key: ValueKey('batch-number-${model.id}'),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 7,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: const Color(0xff173426),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      '${model.id == 'publisher' ? 'Unit' : 'Batch'} ${model.batchNumber}',
                      style: const TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.w900,
                      ),
                    ),
                  ),
                ],
                const SizedBox(width: 6),
                Container(
                  key: ValueKey('gate-signal-${model.id}'),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 7,
                    vertical: 4,
                  ),
                  decoration: BoxDecoration(
                    color: color.withValues(alpha: 0.10),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: color.withValues(alpha: 0.55)),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.radar_rounded, size: 12, color: color),
                      const SizedBox(width: 4),
                      Text(
                        'Gate ${gate.current}/${gate.total} · ${gateSeconds}s',
                        key: ValueKey('gate-text-${model.id}'),
                        style: TextStyle(
                          fontSize: 10,
                          color: color,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 7),
            Align(
              alignment: Alignment.centerRight,
              child: Wrap(
                alignment: WrapAlignment.end,
                crossAxisAlignment: WrapCrossAlignment.center,
                spacing: 7,
                runSpacing: 7,
                children: [
                  OutlinedButton.icon(
                    key: ValueKey('exact-stage-button-${model.id}'),
                    onPressed: () => _showExactStage(context, gate),
                    icon: const Icon(Icons.account_tree_outlined, size: 15),
                    label: const Text('Exact stage'),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: color,
                      side: BorderSide(color: color),
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                  if (completionLabel.isNotEmpty)
                    Container(
                      key: ValueKey('completion-pill-${model.id}'),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 11,
                        vertical: 7,
                      ),
                      decoration: BoxDecoration(
                        gradient: const LinearGradient(
                          colors: [
                            Color(0xffef476f),
                            Color(0xffffd166),
                            Color(0xff35d07f),
                            Color(0xff4cc9f0),
                            Color(0xffb06cff),
                          ],
                        ),
                        borderRadius: BorderRadius.circular(18),
                        boxShadow: [
                          BoxShadow(
                            color: const Color(
                              0xff4cc9f0,
                            ).withValues(alpha: 0.35),
                            blurRadius: 14,
                            spreadRadius: 1,
                          ),
                        ],
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            completionState == 'published'
                                ? Icons.celebration_rounded
                                : Icons.inventory_2_rounded,
                            size: 15,
                            color: const Color(0xff06160f),
                          ),
                          const SizedBox(width: 5),
                          Text(
                            completionLabel,
                            style: const TextStyle(
                              color: Color(0xff06160f),
                              fontSize: 11,
                              fontWeight: FontWeight.w900,
                            ),
                          ),
                        ],
                      ),
                    ),
                  AnimatedContainer(
                    key: ValueKey('active-pill-${model.id}'),
                    duration: const Duration(milliseconds: 350),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 7,
                    ),
                    decoration: BoxDecoration(
                      color: activityColor.withValues(alpha: 0.14),
                      borderRadius: BorderRadius.circular(18),
                      border: Border.all(color: activityColor),
                      boxShadow: [
                        BoxShadow(
                          color: activityColor.withValues(alpha: 0.38),
                          blurRadius: 12,
                          spreadRadius: 1,
                        ),
                      ],
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          uiStuck
                              ? Icons.error_outline_rounded
                              : uiActive
                              ? Icons.bolt_rounded
                              : publisherIdle
                              ? Icons.pause_circle_outline_rounded
                              : Icons.hourglass_empty_rounded,
                          size: 15,
                          color: activityColor,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          activityLabel,
                          style: TextStyle(
                            color: activityColor,
                            fontSize: 11,
                            fontWeight: FontWeight.w900,
                          ),
                        ),
                      ],
                    ),
                  ),
                  OutlinedButton.icon(
                    key: ValueKey('mode-pill-${model.id}'),
                    onPressed: () => _showModeDetails(context, heldFor),
                    icon: Icon(
                      model.mode == 'uploading'
                          ? Icons.cloud_upload_outlined
                          : model.id == 'publisher'
                          ? Icons.verified_outlined
                          : Icons.travel_explore_rounded,
                      size: 15,
                    ),
                    label: Text(_modeButtonLabel(heldFor)),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: modeColor,
                      side: BorderSide(color: modeColor),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 7,
                      ),
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Pipeline ${gate.current}/${gate.total} · $pipelineLabel',
                    key: ValueKey('pipeline-label-${model.id}'),
                    style: const TextStyle(
                      fontSize: 11,
                      color: Color(0xff83a891),
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ),
                Text(
                  '${(overallProgress * 100).toStringAsFixed(0)}%',
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w900,
                  ),
                ),
              ],
            ),
            if (!substageComplete) const SizedBox(height: 5),
            if (!substageComplete)
              LinearProgressIndicator(
                key: ValueKey('pipeline-meter-${model.id}'),
                value: overallProgress,
                minHeight: 5,
                borderRadius: BorderRadius.circular(8),
                backgroundColor: const Color(0xff173426),
                color: color,
              ),
            if (!substageComplete) const SizedBox(height: 10),
            if (!substageComplete)
              Row(
                children: [
                  Expanded(
                    child: Text(
                      'Substage ${activeSubstage + 1}/${activeSubstages.length} · $substageProgressLabel',
                      key: ValueKey('card-substage-label-${model.id}'),
                      style: const TextStyle(
                        fontSize: 11,
                        color: Color(0xffa9c8b5),
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                  Text(
                    substageProgress == null
                        ? '0.0% measured'
                        : '${((1 - substageProgress) * 100).toStringAsFixed(1)}% left',
                    key: ValueKey('card-substage-remaining-${model.id}'),
                    style: const TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                ],
              ),
            if (!substageComplete) const SizedBox(height: 5),
            if (!substageComplete)
              LinearProgressIndicator(
                key: ValueKey('card-substage-meter-${model.id}'),
                value: substageProgress ?? 0.0,
                minHeight: 5,
                borderRadius: BorderRadius.circular(8),
                backgroundColor: const Color(0xff173426),
                color: const Color(0xff4cc9f0),
              ),
            const SizedBox(height: 14),
            Text(
              model.mode == 'discovery' && pagesCompleted != null
                  ? uiStuck
                        ? 'Stuck at $pagesCompleted pages scanned'
                        : '$pagesCompleted pages scanned'
                  : '${model.accepted.toString()} / ${model.target.toString()}',
              key: ValueKey('primary-counter-${model.id}'),
              style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800),
            ),
            if (model.id != 'publisher' && model.candidateTarget > 0) ...[
              const SizedBox(height: 5),
              Text(
                candidateCounterLabel(model),
                key: ValueKey('candidate-progress-${model.id}'),
                style: const TextStyle(
                  color: Color(0xff64dc98),
                  fontSize: 13,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ],
            if (model.id == 'publisher' &&
                publisherCampaignLabel(model) != null) ...[
              const SizedBox(height: 5),
              Text(
                publisherCampaignLabel(model)!,
                key: const ValueKey('publisher-campaign-progress'),
                style: const TextStyle(
                  color: Color(0xff64dc98),
                  fontSize: 13,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ],
            if (uiStuck) ...[
              const SizedBox(height: 6),
              Text(
                'Why: ${model.detail}',
                key: ValueKey('stuck-reason-${model.id}'),
                style: const TextStyle(
                  color: Color(0xffff8a9a),
                  fontSize: 12,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ],
            if (model.mode != 'discovery' && gateProgress == null) ...[
              const SizedBox(height: 7),
              LinearProgressIndicator(
                key: ValueKey('active-work-meter-${model.id}'),
                value: progress,
                minHeight: 7,
                borderRadius: BorderRadius.circular(8),
                backgroundColor: const Color(0xff173426),
                color: color,
              ),
            ],
            if (gateProgress != null) ...[
              const SizedBox(height: 11),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      '$gateProgressLabel · $gateProgressCurrent / $gateProgressTarget',
                      key: ValueKey('gate-loading-label-${model.id}'),
                      style: const TextStyle(
                        fontSize: 11,
                        color: Color(0xff83a891),
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  Text(
                    '${(gateProgress * 100).toStringAsFixed(1)}%',
                    key: ValueKey('gate-loading-percent-${model.id}'),
                    style: const TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 5),
              LinearProgressIndicator(
                key: ValueKey('gate-loading-meter-${model.id}'),
                value: gateProgress,
                minHeight: 5,
                borderRadius: BorderRadius.circular(8),
                backgroundColor: const Color(0xff173426),
                color: const Color(0xff4cc9f0),
              ),
            ],
            const SizedBox(height: 9),
            Text(
              discoverySummary ??
                  '${(progress * 100).toStringAsFixed(1)}% · ${model.uploaded} uploaded',
              softWrap: true,
            ),
            if (heldFor.inSeconds >= 10) ...[
              const SizedBox(height: 4),
              Row(
                children: [
                  const Icon(
                    Icons.timer_outlined,
                    size: 14,
                    color: Color(0xff83a891),
                  ),
                  const SizedBox(width: 5),
                  Expanded(
                    child: Text(
                      'At ${(progress * 100).toStringAsFixed(1)}% for ${formatDuration(heldFor)}',
                      key: ValueKey('progress-duration-${model.id}'),
                      softWrap: true,
                      style: const TextStyle(
                        fontSize: 11,
                        color: Color(0xff83a891),
                      ),
                    ),
                  ),
                ],
              ),
            ],
            const SizedBox(height: 5),
            Text(
              model.detail,
              softWrap: true,
              style: const TextStyle(fontSize: 12, color: Color(0xff83a891)),
            ),
            if (model.exhaustedSource) ...[
              const SizedBox(height: 8),
              Container(
                key: ValueKey('exhausted-source-${model.id}'),
                padding: const EdgeInsets.symmetric(
                  horizontal: 10,
                  vertical: 8,
                ),
                decoration: BoxDecoration(
                  color: const Color(0xff351416),
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: const Color(0xffff4d5f)),
                ),
                child: Text(
                  'EXHAUSTED SOURCE · ${model.acceptanceRate?.toStringAsFixed(3) ?? '0.000'}% accepted',
                  key: ValueKey('exhausted-rate-${model.id}'),
                  style: const TextStyle(
                    color: Color(0xffff5f6d),
                    fontSize: 12,
                    fontWeight: FontWeight.w900,
                  ),
                ),
              ),
            ],
            if (model.id == 'publisher' && batchQueue.isNotEmpty) ...[
              const SizedBox(height: 10),
              Container(
                key: const ValueKey('publisher-batch-queue'),
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: const Color(0xff0c2017),
                  borderRadius: BorderRadius.circular(11),
                  border: Border.all(color: const Color(0xff28533d)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      'BATCHES READY TO ROLL',
                      style: TextStyle(
                        color: Color(0xff83cda4),
                        fontSize: 11,
                        fontWeight: FontWeight.w900,
                        letterSpacing: 0.7,
                      ),
                    ),
                    const SizedBox(height: 7),
                    for (var index = 0; index < batchQueue.length; index++) ...[
                      if (index > 0) const SizedBox(height: 6),
                      _publisherBatchRow(batchQueue[index], index),
                    ],
                  ],
                ),
              ),
            ],
            const SizedBox(height: 10),
            Wrap(
              alignment: WrapAlignment.spaceBetween,
              runAlignment: WrapAlignment.spaceBetween,
              spacing: 8,
              runSpacing: 8,
              children: [
                if (model.id == 'google-books' &&
                    model.stage != 'source-access-preflight')
                  Tooltip(
                    message: 'Fresh receipt-safe reset of this lane only',
                    child: FilledButton.icon(
                      key: ValueKey('clean-reset-${model.id}'),
                      onPressed: onCleanReset,
                      icon: const Icon(
                        Icons.cleaning_services_rounded,
                        size: 16,
                      ),
                      label: const Text('Clean reset'),
                    ),
                  ),
                Tooltip(
                  message:
                      'Request an immediate, receipt-preserving lane reset',
                  child: OutlinedButton.icon(
                    key: ValueKey('emergency-reset-${model.id}'),
                    onPressed: onEmergencyReset,
                    icon: const Icon(Icons.restart_alt_rounded, size: 16),
                    label: const Text('Emergency reset'),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: const Color(0xffff8a3d),
                      side: const BorderSide(color: Color(0xffff8a3d)),
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                ),
                Tooltip(
                  message: pushReady
                      ? 'Freeze ${model.accepted} approved books and hand them to protected staging'
                      : model.id == 'publisher'
                      ? 'The publisher advances automatically'
                      : 'Available as soon as this lane has approved survivors',
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 250),
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(10),
                      boxShadow: pushReady
                          ? [
                              BoxShadow(
                                color: color.withValues(alpha: 0.35),
                                blurRadius: 10,
                                spreadRadius: 1,
                              ),
                            ]
                          : const [],
                    ),
                    child: FilledButton.tonalIcon(
                      key: ValueKey('push-${model.id}'),
                      onPressed: pushReady ? onPush : null,
                      icon: const Icon(Icons.fast_forward_rounded, size: 16),
                      label: const Text('Push'),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

String formatDuration(Duration duration) {
  final seconds = duration.inSeconds.clamp(0, 1 << 31);
  final hours = seconds ~/ 3600;
  final minutes = (seconds % 3600) ~/ 60;
  final remainder = seconds % 60;
  if (hours > 0) return '${hours}h ${minutes.toString().padLeft(2, '0')}m';
  if (minutes > 0) {
    return '${minutes}m ${remainder.toString().padLeft(2, '0')}s';
  }
  return '${remainder}s';
}

String formatActivityAge(Duration duration) {
  if (duration.inSeconds < 60) return '${duration.inSeconds.clamp(0, 59)}s';
  if (duration.inMinutes < 60) return '${duration.inMinutes}m';
  return '${duration.inHours}h';
}

class Metric extends StatelessWidget {
  const Metric({
    super.key,
    required this.label,
    required this.value,
    required this.icon,
  });
  final String label;
  final String value;
  final IconData icon;

  @override
  Widget build(BuildContext context) => Container(
    constraints: const BoxConstraints(minWidth: 138),
    padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 11),
    decoration: BoxDecoration(
      color: const Color(0xff0d1d16),
      borderRadius: BorderRadius.circular(14),
      border: Border.all(color: const Color(0xff1e4733)),
    ),
    child: Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 19, color: const Color(0xff64dc98)),
        const SizedBox(width: 8),
        Flexible(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                value,
                maxLines: 1,
                style: const TextStyle(
                  fontWeight: FontWeight.w800,
                  fontSize: 16,
                ),
              ),
              Text(
                label,
                softWrap: true,
                style: const TextStyle(fontSize: 10, color: Color(0xff83a891)),
              ),
            ],
          ),
        ),
      ],
    ),
  );
}

class LogRow extends StatelessWidget {
  const LogRow({
    super.key,
    required this.time,
    required this.text,
    required this.color,
  });
  final String time;
  final String text;
  final Color color;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 7),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 38,
          child: Text(
            time,
            style: const TextStyle(fontSize: 11, color: Color(0xff83a891)),
          ),
        ),
        Container(
          width: 7,
          height: 7,
          margin: const EdgeInsets.only(top: 5),
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 9),
        Expanded(child: Text(text, softWrap: true)),
      ],
    ),
  );
}

class MemoryWaitingGame extends StatefulWidget {
  const MemoryWaitingGame({super.key});

  @override
  State<MemoryWaitingGame> createState() => _MemoryWaitingGameState();
}

class _MemoryWaitingGameState extends State<MemoryWaitingGame> {
  final _random = Random();
  final List<int> _sequence = [];
  final List<int> _entered = [];
  int? _litPad;
  int _score = 0;
  int _highScore = 0;
  bool _playingSequence = false;
  bool _milestoneShown = false;
  String _message = 'Press Start, watch the pads, then repeat.';

  @override
  void initState() {
    super.initState();
    _restoreGame();
  }

  Future<void> _restoreGame() async {
    final preferences = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _highScore = preferences.getInt('waiting_game_high_score') ?? 0;
      _milestoneShown =
          preferences.getBool('waiting_game_milestone_20') ?? false;
    });
  }

  Future<void> _start() async {
    if (_playingSequence) return;
    setState(() {
      _sequence.clear();
      _entered.clear();
      _score = 0;
      _message = 'Watch…';
    });
    await _nextRound();
  }

  Future<void> _nextRound() async {
    _sequence.add(_random.nextInt(4) + 1);
    _entered.clear();
    setState(() {
      _playingSequence = true;
      _message =
          'Watch ${_sequence.length} ${_sequence.length == 1 ? 'number' : 'numbers'}…';
    });
    await Future<void>.delayed(const Duration(milliseconds: 450));
    for (final pad in _sequence) {
      if (!mounted) return;
      setState(() => _litPad = pad);
      await Future<void>.delayed(const Duration(milliseconds: 360));
      if (!mounted) return;
      setState(() => _litPad = null);
      await Future<void>.delayed(const Duration(milliseconds: 160));
    }
    if (!mounted) return;
    setState(() {
      _playingSequence = false;
      _message = 'Your turn';
    });
  }

  Future<void> _tap(int pad) async {
    if (_playingSequence || _sequence.isEmpty) return;
    final expected = _sequence[_entered.length];
    setState(() {
      _litPad = pad;
      _entered.add(pad);
    });
    Timer(const Duration(milliseconds: 160), () {
      if (mounted && _litPad == pad) setState(() => _litPad = null);
    });
    if (pad != expected) {
      setState(() => _message = 'Not quite. Press Start to try again.');
      return;
    }
    if (_entered.length != _sequence.length) return;

    _score += 1;
    if (_score > _highScore) {
      _highScore = _score;
      final preferences = await SharedPreferences.getInstance();
      await preferences.setInt('waiting_game_high_score', _highScore);
    }
    if (_score >= 20 && !_milestoneShown && mounted) {
      _milestoneShown = true;
      final preferences = await SharedPreferences.getInstance();
      await preferences.setBool('waiting_game_milestone_20', true);
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Chris was innocent.')));
    }
    if (!mounted) return;
    setState(() => _message = 'Correct! Round ${_score + 1}');
    await Future<void>.delayed(const Duration(milliseconds: 450));
    if (mounted) await _nextRound();
  }

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(15),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'WAITING GAME',
                      style: TextStyle(
                        fontWeight: FontWeight.w900,
                        color: Color(0xff64dc98),
                      ),
                    ),
                    Text(
                      'Repeat the four-pad sequence',
                      softWrap: true,
                      style: TextStyle(fontSize: 11, color: Color(0xff83a891)),
                    ),
                  ],
                ),
              ),
              Text(
                '$_score · BEST $_highScore',
                style: const TextStyle(
                  fontWeight: FontWeight.w800,
                  fontSize: 12,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            _message,
            textAlign: TextAlign.center,
            softWrap: true,
            style: const TextStyle(fontSize: 12),
          ),
          const SizedBox(height: 10),
          GridView.count(
            crossAxisCount: 2,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 8,
            crossAxisSpacing: 8,
            childAspectRatio: 2.05,
            children: [for (var pad = 1; pad <= 4; pad++) _pad(pad)],
          ),
          const SizedBox(height: 10),
          OutlinedButton.icon(
            onPressed: _playingSequence ? null : _start,
            icon: const Icon(Icons.play_arrow_rounded),
            label: Text(_sequence.isEmpty ? 'Start' : 'Restart'),
          ),
        ],
      ),
    ),
  );

  Widget _pad(int pad) {
    final lit = _litPad == pad;
    return Semantics(
      key: ValueKey('memory-pad-$pad'),
      button: true,
      label: 'Memory pad $pad',
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () => _tap(pad),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 100),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: lit ? const Color(0xff63f5a4) : const Color(0xff173c2a),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: lit ? Colors.white : const Color(0xff2e6c4c),
              width: lit ? 2 : 1,
            ),
            boxShadow: lit
                ? const [BoxShadow(color: Color(0x9935d07f), blurRadius: 16)]
                : null,
          ),
          child: Text(
            '$pad',
            style: TextStyle(
              fontSize: 22,
              fontWeight: FontWeight.w900,
              color: lit ? const Color(0xff052516) : null,
            ),
          ),
        ),
      ),
    );
  }
}
