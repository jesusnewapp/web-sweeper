# Inquiry Flutter client

The cross-platform mobile and desktop client for Web Sweeper's optional Inquiry
interface. It searches staged and live operator-owned records and selects reader,
audio, video, image, ROM-player, software-inspection, or fallback adapters from
each record's `media` object.

See the parent [`README.md`](../README.md) for the record contract, safe adapter
configuration, server options, and security boundaries.

```bash
flutter pub get
flutter run --dart-define=INQUIRY_ENDPOINT=https://your-server.example/api/records
```

No service-account keys, emulator binaries, collection data, or production-write
authority belong in this client.
