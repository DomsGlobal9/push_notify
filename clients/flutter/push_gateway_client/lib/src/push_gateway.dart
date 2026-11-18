import 'dart:async';

import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

import 'api.dart';
import 'config.dart';
import 'installation.dart';

/// A push the user tapped, or one that arrived while the app was open.
@immutable
class PushMessage {
  const PushMessage({required this.data, this.title, this.body, this.notificationId});

  factory PushMessage.fromRemote(RemoteMessage m) {
    final data = Map<String, String>.fromEntries(
        m.data.entries.where((e) => e.key != '_ngw_id').map((e) => MapEntry(e.key, '${e.value}')));
    return PushMessage(
      data: data,
      title: m.notification?.title,
      body: m.notification?.body,
      notificationId: m.data['_ngw_id']?.toString(),
    );
  }

  final Map<String, String> data;
  final String? title;
  final String? body;

  /// The gateway's notification id, used for open tracking.
  final String? notificationId;
}

/// Wires Firebase Messaging (token + delivery on Android, delivery on iOS) to the gateway.
///
/// Call [start] once per launch after `Firebase.initializeApp()`. It is safe to call
/// repeatedly; registration is an upsert.
class PushGateway {
  PushGateway(this.config, {GatewayApi? api, InstallationStore? store, FirebaseMessaging? messaging})
      : api = api ?? GatewayApi(baseUrl: config.baseUrl, publishableKey: config.publishableKey),
        _store = store ?? InstallationStore(),
        _messagingOverride = messaging;

  final PushGatewayConfig config;
  final GatewayApi api;
  final InstallationStore _store;
  final FirebaseMessaging? _messagingOverride;

  FirebaseMessaging get _messaging => _messagingOverride ?? FirebaseMessaging.instance;

  final _opened = StreamController<PushMessage>.broadcast();
  final _foreground = StreamController<PushMessage>.broadcast();
  final _subs = <StreamSubscription<dynamic>>[];
  bool _started = false;
  PushMessage? _initial;

  /// Pushes the user tapped (app in background or launched from the push).
  Stream<PushMessage> get opened => _opened.stream;

  /// Pushes that arrived while the app was in the foreground. The OS does not show
  /// these on Android; show your own in-app banner or local notification.
  Stream<PushMessage> get foreground => _foreground.stream;

  /// The push that launched the app from terminated state, if any. Read it once
  /// your router is ready.
  PushMessage? takeInitialMessage() {
    final m = _initial;
    _initial = null;
    return m;
  }

  bool get _isIos => defaultTargetPlatform == TargetPlatform.iOS;
  String get _platform => _isIos ? 'ios' : 'android';
  String get _bundleId => _isIos ? config.iosBundleId : config.androidPackage;

  Future<bool> requestPermission() async {
    final s = await _messaging.requestPermission();
    return s.authorizationStatus == AuthorizationStatus.authorized ||
        s.authorizationStatus == AuthorizationStatus.provisional;
  }

  /// Ask for permission, begin listening, and register this install, keeping
  /// whichever user was linked before. Prefer [startAs] in an app with sign-in.
  Future<void> start() => _start();

  /// Like [start], but sets who this install belongs to *before* the first
  /// registration, so a phone is never briefly linked to the previous person
  /// (a signed-out session that never called [signOut]). Pass nulls when the
  /// backend sent no identity: the phone then registers anonymously.
  Future<void> startAs({String? userId, String? userToken}) async {
    final hasUser = userId != null && userId.isNotEmpty && userToken != null && userToken.isNotEmpty;
    await _store.setUser(hasUser ? userId : null, hasUser ? userToken : null);
    await _start();
  }

  Future<void> _start() async {
    if (config.requestPermissionOnStart) await requestPermission();
    if (!_started) {
      _started = true;
      _subs
        ..add(_messaging.onTokenRefresh.listen((_) => unawaited(_registerSafely())))
        ..add(FirebaseMessaging.onMessageOpenedApp.listen((m) => _handleOpened(PushMessage.fromRemote(m))))
        ..add(FirebaseMessaging.onMessage.listen((m) => _foreground.add(PushMessage.fromRemote(m))));
      final initial = await _messaging.getInitialMessage();
      if (initial != null) {
        _initial = PushMessage.fromRemote(initial);
        unawaited(_report(_initial!));
      }
    }
    await _registerSafely();
  }

  /// Link this device to a signed-in user. [userToken] comes from your backend:
  /// HMAC_SHA256(identity_secret, userId) as hex.
  Future<void> signIn({required String userId, required String userToken}) async {
    await _store.setUser(userId, userToken);
    await _register();
  }

  /// Unlink the user; the device keeps receiving broadcasts.
  Future<void> signOut() async {
    await _store.setUser(null, null);
    await api.logout(await _store.installationId(), _bundleId);
  }

  Future<List<String>> subscribe(List<String> topics) async {
    final result = await api.updateTopics(await _store.installationId(), _bundleId, add: topics);
    await _store.setTopics(result);
    return result;
  }

  Future<List<String>> unsubscribe(List<String> topics) async {
    final result = await api.updateTopics(await _store.installationId(), _bundleId, remove: topics);
    await _store.setTopics(result);
    return result;
  }

  /// Forget this install on the gateway (for example, the user turned notifications off in your app).
  Future<void> unregister() async => api.unregister(await _store.installationId(), _bundleId);

  Future<void> dispose() async {
    for (final s in _subs) {
      await s.cancel();
    }
    await _opened.close();
    await _foreground.close();
  }

  // ---------------------------------------------------------------------------

  Future<String?> _pushToken() async {
    if (_isIos && config.iosTransport == IosTransport.apnsDirect) {
      // The APNs token can arrive a moment after launch.
      for (var i = 0; i < 10; i++) {
        final t = await _messaging.getAPNSToken();
        if (t != null) return t;
        await Future<void>.delayed(const Duration(milliseconds: 500));
      }
      return null;
    }
    return _messaging.getToken();
  }

  Future<void> _register() async {
    final token = await _pushToken();
    if (token == null) {
      debugPrint('push_gateway: no push token yet (permission denied or no network)');
      return;
    }
    final user = await _store.user();
    await api.registerDevice(
      installationId: await _store.installationId(),
      platform: _platform,
      bundleId: _bundleId,
      token: token,
      apnsEnvironment: config.apnsSandbox ? 'sandbox' : 'production',
      userId: user.userId,
      userToken: user.userToken,
      locale: PlatformDispatcher.instance.locale.toLanguageTag(),
      timezone: DateTime.now().timeZoneName,
      appVersion: config.appVersion,
    );
  }

  Future<void> _registerSafely() async {
    try {
      await _register();
    } on Object catch (e) {
      // Never crash the app over push registration; the next launch retries.
      debugPrint('push_gateway: registration failed: $e');
    }
  }

  void _handleOpened(PushMessage m) {
    _opened.add(m);
    unawaited(_report(m));
  }

  Future<void> _report(PushMessage m) async {
    final id = m.notificationId;
    if (id == null) return;
    try {
      await api.reportOpened(id, installationId: await _store.installationId());
    } on Object catch (e) {
      debugPrint('push_gateway: open tracking failed: $e');
    }
  }
}
