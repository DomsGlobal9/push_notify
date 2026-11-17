import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:push_gateway_client/push_gateway_client.dart';

/// Records requests and answers from a script, so no network is needed.
class FakeAdapter implements HttpClientAdapter {
  FakeAdapter(this.respond);

  final ResponseBody Function(RequestOptions) respond;
  final requests = <RequestOptions>[];

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    requests.add(options);
    return respond(options);
  }

  @override
  void close({bool force = false}) {}
}

ResponseBody json(int status, Object body) => ResponseBody.fromString(jsonEncode(body), status,
    headers: {Headers.contentTypeHeader: [Headers.jsonContentType]});

void main() {
  late FakeAdapter adapter;
  late GatewayApi api;

  setUp(() {
    adapter = FakeAdapter((o) => json(200, {'ok': true, 'topics': ['offers']}));
    final dio = Dio()..httpClientAdapter = adapter;
    api = GatewayApi(baseUrl: 'https://push.example.com/', publishableKey: 'pk_test', dio: dio);
  });

  test('registerDevice sends key, path and body', () async {
    await api.registerDevice(
      installationId: 'abcdef0123456789',
      platform: 'android',
      bundleId: 'com.example.shop',
      token: 'fcm-token-123',
      userId: 'cust-42',
      userToken: 'hmac',
    );
    final r = adapter.requests.single;
    expect(r.uri.toString(), 'https://push.example.com/v1/devices');
    expect(r.headers['X-API-Key'], 'pk_test');
    expect(r.data, containsPair('user_external_id', 'cust-42'));
    expect(r.data, isNot(contains('locale')));
  });

  test('logout and unregister pass bundle_id', () async {
    await api.logout('install-1', 'com.example.shop');
    await api.unregister('install-1', 'com.example.shop');
    expect(adapter.requests[0].uri.toString(),
        'https://push.example.com/v1/devices/install-1/logout?bundle_id=com.example.shop');
    expect(adapter.requests[1].method, 'DELETE');
  });

  test('updateTopics returns the server list', () async {
    expect(await api.updateTopics('install-1', 'com.example.shop', add: ['offers']), ['offers']);
  });

  test('errors carry the gateway code', () async {
    adapter = FakeAdapter((o) => json(403, {'error': 'user_token mismatch', 'code': 'permission_denied'}));
    final dio = Dio()..httpClientAdapter = adapter;
    api = GatewayApi(baseUrl: 'https://push.example.com', publishableKey: 'pk', dio: dio);
    await expectLater(
      api.reportOpened('n1'),
      throwsA(isA<GatewayException>()
          .having((e) => e.statusCode, 'status', 403)
          .having((e) => e.code, 'code', 'permission_denied')),
    );
  });
}
