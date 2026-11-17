/// Client for the self-hosted Push Gateway.
///
/// ```dart
/// final push = PushGateway(PushGatewayConfig(
///   baseUrl: 'https://push-gateway.onrender.com',
///   publishableKey: 'pk_...',
///   androidPackage: 'com.example.shop',
///   iosBundleId: 'com.example.shop',
/// ));
/// await push.start();                                   // on every app launch
/// await push.signIn(userId: 'cust-42', userToken: tokenFromYourBackend);
/// push.opened.listen((m) => router.go(m.data['route'] ?? '/'));
/// ```
library;

export 'src/api.dart' show GatewayApi, GatewayException;
export 'src/config.dart';
export 'src/installation.dart' show InstallationStore;
export 'src/push_gateway.dart';
