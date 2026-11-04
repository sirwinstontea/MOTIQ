#!/bin/sh
# Container entrypoint. EXPO_PUBLIC_* values are baked into the JS bundle when Metro builds it here at runtime,
# so they must be set as *runtime* env vars in Coolify. Log which ones the container can see (values masked).
echo "[motiq] env check:"
for v in EXPO_PUBLIC_XAI_API_KEY EXPO_PUBLIC_TOKEN_URL EXPO_PUBLIC_GROK_VOICE_MODEL EXPO_PUBLIC_VISION_URL EXPO_PACKAGER_PROXY_URL; do
  val=$(printenv "$v")
  if [ -n "$val" ]; then echo "  $v = set (${#val} chars)"; else echo "  $v = MISSING"; fi
done
if [ -z "$EXPO_PUBLIC_XAI_API_KEY" ] && [ -z "$EXPO_PUBLIC_TOKEN_URL" ]; then
  echo "[motiq] WARNING: no xAI key or token URL - Talk will fail. Mark the vars as runtime in Coolify and redeploy."
fi

# Warm the bundle once at startup so the first Expo Go open isn't slow.
(sleep 15; node -e "fetch('http://127.0.0.1:'+process.env.PORT+'/index.ts.bundle?platform=android&dev=false&hot=false&lazy=true&minify=true&transform.engine=hermes&transform.bytecode=1&transform.routerRoot=app&unstable_transformProfile=hermes-stable').catch(()=>{})") &

exec npx expo start --port "$PORT" --no-dev --minify --clear
