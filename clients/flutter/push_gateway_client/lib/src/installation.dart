import 'dart:math';

import 'package:shared_preferences/shared_preferences.dart';

/// The install's identity on the gateway: a random id created once and kept
/// for the life of the install. Push tokens rotate; this does not.
class InstallationStore {
  InstallationStore({SharedPreferencesAsync? prefs}) : _prefs = prefs ?? SharedPreferencesAsync();

  static const _idKey = 'push_gateway.installation_id';
  static const _userKey = 'push_gateway.user_id';
  static const _userTokenKey = 'push_gateway.user_token';
  static const _topicsKey = 'push_gateway.topics';

  final SharedPreferencesAsync _prefs;

  Future<String> installationId() async {
    final existing = await _prefs.getString(_idKey);
    if (existing != null && existing.length >= 8) return existing;
    final rnd = Random.secure();
    final id = List.generate(16, (_) => rnd.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
    await _prefs.setString(_idKey, id);
    return id;
  }

  Future<({String? userId, String? userToken})> user() async =>
      (userId: await _prefs.getString(_userKey), userToken: await _prefs.getString(_userTokenKey));

  Future<void> setUser(String? userId, String? userToken) async {
    if (userId == null) {
      await _prefs.remove(_userKey);
      await _prefs.remove(_userTokenKey);
    } else {
      await _prefs.setString(_userKey, userId);
      if (userToken != null) await _prefs.setString(_userTokenKey, userToken);
    }
  }

  Future<List<String>?> topics() => _prefs.getStringList(_topicsKey);

  Future<void> setTopics(List<String> topics) => _prefs.setStringList(_topicsKey, topics);
}
