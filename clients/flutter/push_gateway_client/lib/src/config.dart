import 'package:flutter/foundation.dart';

/// How iOS devices receive pushes.
enum IosTransport {
  /// The gateway talks to Apple directly with your .p8 key (recommended).
  /// The app registers its raw APNs device token.
  apnsDirect,

  /// The gateway sends through FCM, which forwards to APNs. The app registers
  /// its FCM token, and the iOS app must be registered on the gateway with an
  /// FCM credential (and the .p8 uploaded to Firebase).
  fcm,
}

@immutable
class PushGatewayConfig {
  const PushGatewayConfig({
    required this.baseUrl,
    required this.publishableKey,
    required this.androidPackage,
    required this.iosBundleId,
    this.iosTransport = IosTransport.apnsDirect,
    this.apnsSandbox = kDebugMode,
    this.requestPermissionOnStart = true,
  });

  /// e.g. https://push-gateway.onrender.com (no trailing slash needed).
  final String baseUrl;

  /// The tenant's `pk_` key. Safe to ship in the app; it cannot send.
  final String publishableKey;

  /// Android package name as registered on the gateway.
  final String androidPackage;

  /// iOS bundle id as registered on the gateway.
  final String iosBundleId;

  final IosTransport iosTransport;

  /// Debug builds get sandbox APNs tokens; release/TestFlight builds get production ones.
  final bool apnsSandbox;

  /// Ask for notification permission inside [PushGateway.start].
  /// Set false to ask later, at a moment that explains why (better opt-in rates),
  /// then call [PushGateway.requestPermission].
  final bool requestPermissionOnStart;
}
