import 'package:dio/dio.dart';

/// An error answer from the gateway: `code` is stable, `message` is for logs.
class GatewayException implements Exception {
  GatewayException(this.statusCode, this.code, this.message);

  final int? statusCode;
  final String code;
  final String message;

  @override
  String toString() => 'GatewayException($statusCode, $code): $message';
}

/// The device-side HTTP API. No Firebase here, so it is easy to test and reuse.
class GatewayApi {
  GatewayApi({required String baseUrl, required String publishableKey, Dio? dio})
      : _dio = dio ?? Dio() {
    _dio.options
      ..baseUrl = baseUrl.endsWith('/') ? baseUrl.substring(0, baseUrl.length - 1) : baseUrl
      ..connectTimeout = const Duration(seconds: 10)
      ..receiveTimeout = const Duration(seconds: 15)
      ..headers['X-API-Key'] = publishableKey
      ..headers['Content-Type'] = 'application/json';
  }

  final Dio _dio;

  Future<void> registerDevice({
    required String installationId,
    required String platform,
    required String bundleId,
    required String token,
    String apnsEnvironment = 'production',
    String? userId,
    String? userToken,
    String? locale,
    String? timezone,
    String? appVersion,
    List<String>? topics,
  }) =>
      _call(() => _dio.post<dynamic>('/v1/devices', data: {
            'installation_id': installationId,
            'platform': platform,
            'bundle_id': bundleId,
            'token': token,
            'apns_environment': apnsEnvironment,
            if (userId != null) 'user_external_id': userId,
            if (userToken != null) 'user_token': userToken,
            if (locale != null) 'locale': locale,
            if (timezone != null) 'timezone': timezone,
            if (appVersion != null) 'app_version': appVersion,
            if (topics != null) 'topics': topics,
          }));

  Future<void> logout(String installationId, String bundleId) => _call(() => _dio.post<dynamic>(
      '/v1/devices/${Uri.encodeComponent(installationId)}/logout',
      queryParameters: {'bundle_id': bundleId}));

  Future<void> unregister(String installationId, String bundleId) => _call(() => _dio.delete<dynamic>(
      '/v1/devices/${Uri.encodeComponent(installationId)}',
      queryParameters: {'bundle_id': bundleId}));

  Future<List<String>> updateTopics(String installationId, String bundleId,
      {List<String> add = const [], List<String> remove = const []}) async {
    final res = await _call(() => _dio.post<dynamic>(
        '/v1/devices/${Uri.encodeComponent(installationId)}/topics',
        data: {'bundle_id': bundleId, 'add': add, 'remove': remove}));
    return ((res.data as Map)['topics'] as List).cast<String>();
  }

  Future<void> reportOpened(String notificationId, {String? installationId}) =>
      _call(() => _dio.post<dynamic>('/v1/events', data: {
            'notification_id': notificationId,
            'type': 'opened',
            if (installationId != null) 'installation_id': installationId,
          }));

  Future<Response<dynamic>> _call(Future<Response<dynamic>> Function() request) async {
    try {
      return await request();
    } on DioException catch (e) {
      final body = e.response?.data;
      if (body is Map) {
        throw GatewayException(
            e.response?.statusCode, '${body['code'] ?? 'error'}', '${body['error'] ?? e.message}');
      }
      throw GatewayException(e.response?.statusCode, 'network', e.message ?? e.type.name);
    }
  }
}
