import 'dart:convert';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:just_audio/just_audio.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:video_player/video_player.dart';

const endpoint = String.fromEnvironment('INQUIRY_ENDPOINT',
    defaultValue: 'http://127.0.0.1:8787/api/records');

void main() => runApp(const InquiryApp());

class InquiryApp extends StatelessWidget {
  const InquiryApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        debugShowCheckedModeBanner: false,
        title: 'Inquiry',
        theme: ThemeData.dark(useMaterial3: true).copyWith(
          scaffoldBackgroundColor: const Color(0xff07110f),
          colorScheme: ColorScheme.fromSeed(
              seedColor: const Color(0xff57efb2), brightness: Brightness.dark),
          textTheme: ThemeData.dark().textTheme.apply(fontFamily: 'Georgia'),
          inputDecorationTheme: InputDecorationTheme(
              filled: true,
              fillColor: const Color(0xff0b1b17),
              border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(14),
                  borderSide: const BorderSide(color: Color(0xff24423a)))),
        ),
        home: const InquiryHome(),
      );
}

class Record {
  Record(this.raw);
  final Map<String, dynamic> raw;
  String get id => '${raw['id'] ?? ''}';
  String get title => '${raw['title'] ?? 'Untitled'}';
  String get author => '${raw['author'] ?? 'Unknown author'}';
  String get scope => '${raw['scope'] ?? 'staged'}';
  String get stage =>
      '${raw['stage'] ?? (scope == 'live' ? 'live-verified' : 'discovered')}';
  String get category => '${raw['category'] ?? 'Uncategorized'}';
  String get date => '${raw['date'] ?? raw['year'] ?? 'No date'}';
  String get description => '${raw['description'] ?? ''}';
  Map<String, dynamic> get media =>
      raw['media'] is Map ? Map<String, dynamic>.from(raw['media']) : {};
  String get mediaKind {
    if (media['kind'] != null) return '${media['kind']}'.toLowerCase();
    return '${media['mimeType'] ?? 'web'}'.split('/').first.toLowerCase();
  }

  String searchable() => jsonEncode(raw).toLowerCase();
}

class InquiryHome extends StatefulWidget {
  const InquiryHome({super.key});
  @override
  State<InquiryHome> createState() => _InquiryHomeState();
}

class _InquiryHomeState extends State<InquiryHome> {
  final query = TextEditingController(),
      author = TextEditingController(),
      from = TextEditingController(),
      to = TextEditingController(),
      customValue = TextEditingController();
  List<Record> all = [];
  String scope = 'all', category = '', stage = '', customField = '';
  bool loading = true;
  String? error;

  @override
  void initState() {
    super.initState();
    load();
    for (final c in [query, author, from, to, customValue]) {
      c.addListener(refresh);
    }
  }

  @override
  void dispose() {
    for (final c in [query, author, from, to, customValue]) {
      c.dispose();
    }
    super.dispose();
  }

  void refresh() => setState(() {});

  Future<void> load() async {
    try {
      final response = await http
          .get(Uri.parse(endpoint))
          .timeout(const Duration(seconds: 20));
      if (response.statusCode ~/ 100 != 2) {
        throw Exception('Server returned ${response.statusCode}');
      }
      final value = jsonDecode(response.body),
          source = value is List ? value : value['records'];
      if (source is! List) {
        throw const FormatException('Expected a records array');
      }
      setState(() {
        all = source
            .whereType<Map>()
            .map((v) => Record(Map<String, dynamic>.from(v)))
            .toList();
        loading = false;
      });
    } catch (e) {
      setState(() {
        error = '$e';
        loading = false;
      });
    }
  }

  List<Record> get results {
    final tokens = query.text
        .trim()
        .toLowerCase()
        .split(RegExp(r'\s+'))
        .where((v) => v.isNotEmpty);
    return all.where((r) {
      final metadata =
          r.raw['metadata'] is Map ? r.raw['metadata'] as Map : const {};
      final custom = metadata[customField] ?? r.raw[customField] ?? '';
      return tokens.every(r.searchable().contains) &&
          (scope == 'all' || r.scope == scope) &&
          (author.text.isEmpty ||
              r.author.toLowerCase().contains(author.text.toLowerCase())) &&
          (category.isEmpty || r.category == category) &&
          (stage.isEmpty || r.stage == stage) &&
          (from.text.isEmpty || r.date.compareTo(from.text) >= 0) &&
          (to.text.isEmpty || r.date.compareTo(to.text) <= 0) &&
          (customField.isEmpty ||
              '$custom'.toLowerCase().contains(customValue.text.toLowerCase()));
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    final rows = results,
        categories = all.map((e) => e.category).toSet().toList()..sort();
    const pipeline = [
      'discovered',
      'qualified',
      'retrieved',
      'converted',
      'validated',
      'published',
      'live-verified'
    ];
    final fields = all
        .expand((r) => r.raw['metadata'] is Map
            ? (r.raw['metadata'] as Map).keys.cast<String>()
            : const Iterable<String>.empty())
        .toSet()
        .toList()
      ..sort();
    return Scaffold(
        body: Stack(children: [
      const Positioned(right: -120, top: -160, child: _Glow()),
      SafeArea(
          child: RefreshIndicator(
              onRefresh: load,
              child: CustomScrollView(slivers: [
                SliverPadding(
                    padding: const EdgeInsets.fromLTRB(20, 30, 20, 18),
                    sliver: SliverToBoxAdapter(
                        child: _Hero(
                            connected: !loading && error == null,
                            count: all.length))),
                SliverPadding(
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                    sliver: SliverToBoxAdapter(
                        child: _Glass(
                            child: Column(children: [
                      TextField(
                          controller: query,
                          decoration: const InputDecoration(
                              labelText: 'INQUIRY',
                              hintText: 'Title, author, subject, phrase…',
                              prefixIcon: Icon(Icons.manage_search_rounded))),
                      const SizedBox(height: 10),
                      Wrap(spacing: 10, runSpacing: 10, children: [
                        _Select('Scope', scope, const ['all', 'staged', 'live'],
                            (v) => setState(() => scope = v)),
                        _Select('Category', category, ['', ...categories],
                            (v) => setState(() => category = v)),
                        _Select(
                            'Pipeline stage',
                            stage,
                            const ['', ...pipeline],
                            (v) => setState(() => stage = v)),
                        _Field('Author', author),
                        _Field('Date from', from),
                        _Field('Date to', to),
                        _Select('Custom field', customField, ['', ...fields],
                            (v) => setState(() => customField = v)),
                        _Field('Custom value', customValue),
                      ])
                    ])))),
                SliverPadding(
                    padding: const EdgeInsets.fromLTRB(22, 24, 22, 12),
                    sliver: SliverToBoxAdapter(
                        child: Text(
                            loading
                                ? 'Connecting…'
                                : error ?? '${rows.length} works in view',
                            style: TextStyle(
                                color: error == null
                                    ? const Color(0xff91aaa2)
                                    : const Color(0xffff8d78),
                                fontSize: 16)))),
                if (!loading && error == null)
                  SliverPadding(
                      padding: const EdgeInsets.fromLTRB(20, 0, 20, 40),
                      sliver: SliverList.separated(
                          itemCount: rows.length,
                          separatorBuilder: (_, __) =>
                              const SizedBox(height: 12),
                          itemBuilder: (_, i) => _RecordCard(
                              record: rows[i],
                              onOpen: () => _showRecord(rows[i])))),
              ])))
    ]));
  }

  void _showRecord(Record record) => showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => FractionallySizedBox(
          heightFactor: .92, child: _PlayerSheet(record: record)));
}

class _Hero extends StatelessWidget {
  const _Hero({required this.connected, required this.count});
  final bool connected;
  final int count;
  @override
  Widget build(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('WEB SWEEPER · COLLECTION INQUIRY',
            style: TextStyle(
                color: Color(0xff57efb2),
                letterSpacing: 2.1,
                fontSize: 10,
                fontWeight: FontWeight.bold)),
        const SizedBox(height: 14),
        const Text('Find the signal.\nOpen the work.',
            style: TextStyle(fontSize: 48, height: .97, letterSpacing: -2)),
        const SizedBox(height: 16),
        Row(children: [
          Icon(Icons.circle,
              size: 9,
              color: connected
                  ? const Color(0xff57efb2)
                  : const Color(0xffe7c679)),
          const SizedBox(width: 8),
          Text(
              connected
                  ? '$count records connected'
                  : 'Connecting to collection',
              style:
                  const TextStyle(color: Color(0xff91aaa2), fontFamily: 'sans'))
        ])
      ]);
}

class _Glow extends StatelessWidget {
  const _Glow();
  @override
  Widget build(BuildContext c) => ImageFiltered(
      imageFilter: ImageFilter.blur(sigmaX: 70, sigmaY: 70),
      child: Container(
          width: 350,
          height: 350,
          decoration: const BoxDecoration(
              shape: BoxShape.circle, color: Color(0x5536d99a))));
}

class _Glass extends StatelessWidget {
  const _Glass({required this.child});
  final Widget child;
  @override
  Widget build(BuildContext c) => Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
          color: const Color(0xdd0a1915),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: const Color(0xff24423a))),
      child: child);
}

class _Field extends StatelessWidget {
  const _Field(this.label, this.controller);
  final String label;
  final TextEditingController controller;
  @override
  Widget build(BuildContext c) => SizedBox(
      width: 155,
      child: TextField(
          controller: controller,
          decoration: InputDecoration(labelText: label)));
}

class _Select extends StatelessWidget {
  const _Select(this.label, this.value, this.values, this.changed);
  final String label, value;
  final List<String> values;
  final ValueChanged<String> changed;
  @override
  Widget build(BuildContext c) => SizedBox(
      width: 155,
      child: DropdownButtonFormField<String>(
          isExpanded: true,
          initialValue: value,
          decoration: InputDecoration(labelText: label),
          items: values
              .map((v) => DropdownMenuItem(
                  value: v, child: Text(v.isEmpty ? 'Any' : v)))
              .toList(),
          onChanged: (v) => changed(v!)));
}

class _RecordCard extends StatelessWidget {
  const _RecordCard({required this.record, required this.onOpen});
  final Record record;
  final VoidCallback onOpen;
  @override
  Widget build(BuildContext context) => _Glass(
      child: InkWell(
          onTap: onOpen,
          child: Padding(
              padding: const EdgeInsets.all(6),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      _Pill(record.scope, staged: record.scope == 'staged'),
                      const Spacer(),
                      Text(record.stage,
                          style: const TextStyle(
                              color: Color(0xff91aaa2),
                              fontFamily: 'sans',
                              fontSize: 11))
                    ]),
                    const SizedBox(height: 16),
                    Text(record.title,
                        style: const TextStyle(fontSize: 25, height: 1.05)),
                    const SizedBox(height: 7),
                    Text(record.author,
                        style: const TextStyle(
                            color: Color(0xffe7c679), fontFamily: 'sans')),
                    if (record.description.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Text(record.description,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                              color: Color(0xff91aaa2), fontFamily: 'sans'))
                    ],
                    const SizedBox(height: 14),
                    Wrap(spacing: 7, children: [
                      _Pill(record.date),
                      _Pill(record.category),
                      if (record.media.isNotEmpty) _Pill(record.mediaKind)
                    ]),
                  ]))));
}

class _Pill extends StatelessWidget {
  const _Pill(this.text, {this.staged = false});
  final String text;
  final bool staged;
  @override
  Widget build(BuildContext c) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
      decoration: BoxDecoration(
          color: staged ? const Color(0xff3d321c) : const Color(0xff183b30),
          borderRadius: BorderRadius.circular(99)),
      child: Text(text,
          style: TextStyle(
              color: staged ? const Color(0xfff6d77b) : const Color(0xff57efb2),
              fontFamily: 'sans',
              fontSize: 10)));
}

class _PlayerSheet extends StatelessWidget {
  const _PlayerSheet({required this.record});
  final Record record;
  String adapterUrl(Map<String, dynamic> media, String source) {
    final options =
        media['options'] is Map ? media['options'] as Map : const {};
    final template = '${options['playerUrlTemplate'] ?? ''}';
    return template.isEmpty
        ? source
        : template.replaceAll('{url}', Uri.encodeComponent(source));
  }

  Future<void> open(String value) async {
    final uri = Uri.tryParse(value);
    if (uri == null ||
        !await launchUrl(uri, mode: LaunchMode.externalApplication)) {
      throw Exception('Unable to open media');
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = record.media,
        url = '${m['url'] ?? record.raw['url'] ?? ''}',
        kind = record.mediaKind;
    Widget player;
    if (kind == 'image' && url.isNotEmpty) {
      player = ClipRRect(
          borderRadius: BorderRadius.circular(14),
          child: Image.network(url,
              fit: BoxFit.contain,
              errorBuilder: (_, __, ___) => const _Fallback()));
    } else if (kind == 'json') {
      player = SelectableText(
          const JsonEncoder.withIndent('  ')
              .convert(m['content'] ?? record.raw),
          style: const TextStyle(
              fontFamily: 'monospace', color: Color(0xffcde0da)));
    } else if (kind == 'text') {
      player = SelectableText(
          '${m['content'] ?? record.raw['text'] ?? record.description}',
          style: const TextStyle(
              color: Color(0xffcde0da), fontFamily: 'sans', height: 1.6));
    } else if ((kind == 'audio' || kind == 'video') && url.isNotEmpty) {
      player = _InlineMedia(kind: kind, url: url);
    } else {
      final options = m['options'] is Map ? m['options'] as Map : const {};
      final platforms = options['platforms'] is List
          ? (options['platforms'] as List).join(' · ')
          : '';
      player = Column(children: [
        Icon(
            kind == 'rom'
                ? Icons.sports_esports_outlined
                : kind == 'software'
                    ? Icons.inventory_2_outlined
                    : Icons.open_in_new,
            size: 66,
            color: const Color(0xff57efb2)),
        const SizedBox(height: 14),
        Text(
            kind == 'rom'
                ? 'Play with your configured ROM player'
                : kind == 'software'
                    ? 'Software inspection only${platforms.isEmpty ? '' : ' · $platforms'}'
                    : 'Open ${kind == 'web' ? 'reader' : kind}',
            textAlign: TextAlign.center,
            style: const TextStyle(fontFamily: 'sans')),
        const SizedBox(height: 16),
        FilledButton.icon(
            onPressed: url.isEmpty
                ? null
                : () => open(kind == 'rom' ? adapterUrl(m, url) : url),
            icon: Icon(kind == 'rom' ? Icons.play_arrow : Icons.open_in_new),
            label: Text(kind == 'rom' ? 'Play' : 'Open'))
      ]);
    }
    return Container(
        decoration: const BoxDecoration(
            color: Color(0xff0a1815),
            borderRadius: BorderRadius.vertical(top: Radius.circular(28))),
        child: SafeArea(
            child: ListView(padding: const EdgeInsets.all(24), children: [
          Align(
              alignment: Alignment.centerRight,
              child: IconButton(
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.close))),
          Text('${record.scope.toUpperCase()} · ${record.stage}',
              style: const TextStyle(
                  color: Color(0xff57efb2),
                  fontFamily: 'sans',
                  letterSpacing: 1.5,
                  fontSize: 10)),
          const SizedBox(height: 12),
          Text(record.title, style: const TextStyle(fontSize: 38, height: 1)),
          const SizedBox(height: 8),
          Text(record.author,
              style: const TextStyle(
                  color: Color(0xffe7c679), fontFamily: 'sans')),
          const SizedBox(height: 26),
          _Glass(
              child: Padding(padding: const EdgeInsets.all(16), child: player)),
          const SizedBox(height: 22),
          ExpansionTile(title: const Text('Record metadata'), children: [
            SelectableText(
                const JsonEncoder.withIndent('  ').convert(record.raw),
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12))
          ])
        ])));
  }
}

class _Fallback extends StatelessWidget {
  const _Fallback();
  @override
  Widget build(BuildContext c) => const Padding(
      padding: EdgeInsets.all(30), child: Text('Media preview unavailable.'));
}

class _InlineMedia extends StatefulWidget {
  const _InlineMedia({required this.kind, required this.url});
  final String kind, url;
  @override
  State<_InlineMedia> createState() => _InlineMediaState();
}

class _InlineMediaState extends State<_InlineMedia> {
  AudioPlayer? audio;
  VideoPlayerController? video;
  String? error;
  @override
  void initState() {
    super.initState();
    if (widget.kind == 'audio') {
      audio = AudioPlayer();
      audio!.setUrl(widget.url).catchError((e) {
        if (mounted) setState(() => error = '$e');
        return null;
      });
    } else {
      video = VideoPlayerController.networkUrl(Uri.parse(widget.url))
        ..initialize().then((_) {
          if (mounted) setState(() {});
        }).catchError((e) {
          if (mounted) setState(() => error = '$e');
        });
    }
  }

  @override
  void dispose() {
    audio?.dispose();
    video?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (error != null) {
      return Text('Player unavailable: $error',
          style: const TextStyle(color: Color(0xffff8d78), fontFamily: 'sans'));
    }
    if (widget.kind == 'audio') {
      return StreamBuilder<PlayerState>(
          stream: audio!.playerStateStream,
          builder: (context, snapshot) {
            final playing = snapshot.data?.playing ?? false;
            return Column(children: [
              const Icon(Icons.graphic_eq, size: 72, color: Color(0xff57efb2)),
              FilledButton.icon(
                  onPressed: () => playing ? audio!.pause() : audio!.play(),
                  icon: Icon(playing ? Icons.pause : Icons.play_arrow),
                  label: Text(playing ? 'Pause' : 'Play audio'))
            ]);
          });
    }
    if (!(video?.value.isInitialized ?? false)) {
      return const Center(child: CircularProgressIndicator());
    }
    return Column(children: [
      AspectRatio(
          aspectRatio: video!.value.aspectRatio, child: VideoPlayer(video!)),
      const SizedBox(height: 10),
      FilledButton.icon(
          onPressed: () {
            setState(
                () => video!.value.isPlaying ? video!.pause() : video!.play());
          },
          icon: Icon(video!.value.isPlaying ? Icons.pause : Icons.play_arrow),
          label: Text(video!.value.isPlaying ? 'Pause' : 'Play video'))
    ]);
  }
}
