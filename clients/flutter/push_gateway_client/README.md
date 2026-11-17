# push_gateway_client

The Flutter side of the Push Gateway. Every app (SC Boutique, CRM-B mobile, …)
uses this package. It registers the install, links the signed-in user, keeps
the token fresh and reports opens. Delivery on the device uses `firebase_messaging`.

## Add it

```yaml
dependencies:
  firebase_core: ^4.0.0
  firebase_messaging: ^16.0.0
  push_gateway_client:
    git:
      url: <your push-gateway repo>
      path: clients/flutter/push_gateway_client
```

(or `path: ../../Projects/push-gateway/clients/flutter/push_gateway_client` while developing)

## Platform setup (once per app)

**Android**
1. Add the app to your Firebase project and drop `google-services.json` into `android/app/`.
2. Apply the Google services Gradle plugin (FlutterFire docs: *Add Firebase to your Flutter app*).
3. Android 13+: the package's `requestPermission()` triggers the `POST_NOTIFICATIONS` prompt.
4. Create the notification channel (e.g. `orders`) at startup if you use `android_channel_id`.

**iOS**
1. Xcode → Runner → Signing & Capabilities → **+ Push Notifications** and **Background Modes → Remote notifications**.
2. Firebase still needs `GoogleService-Info.plist` because `firebase_messaging` delivers taps to Dart,
   even though the gateway talks to Apple directly.
3. For images in notifications, add a Notification Service Extension that reads the `image` key.

## Use it

```dart
await Firebase.initializeApp();

final push = PushGateway(const PushGatewayConfig(
  baseUrl: 'https://push-gateway.onrender.com',
  publishableKey: 'pk_...',              // safe to ship; cannot send
  androidPackage: 'com.sc.boutique',
  iosBundleId: 'com.sc.boutique',
));

await push.start();                       // every launch; registers or refreshes the install

// after your own login succeeds; userToken comes from YOUR backend's login response
await push.signIn(userId: 'sarala:staff-12', userToken: loginResponse.pushUserToken);

push.opened.listen((m) => router.go(m.data['route'] ?? '/'));   // taps
push.foreground.listen((m) => showBanner(m.title, m.body));     // while app is open
final launchedFrom = push.takeInitialMessage();                  // cold start from a tap

await push.subscribe(['offers']);
await push.signOut();                     // keeps broadcasts, drops user-targeted pushes
```

`start()` never throws for registration problems: it logs and retries on the next
launch, so push setup cannot crash the app.
