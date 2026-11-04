# Runs the Expo bundler so Expo Go can load the app from this server.
# Open in Expo Go:  exps://<your-domain>
FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ENV CI=1 \
    EXPO_NO_TELEMETRY=1 \
    PORT=8081
# Set in Coolify to your public URL, e.g. https://motiq.example.com
# (bundle/asset URLs in the manifest point here instead of the container IP).
# EXPO_PACKAGER_PROXY_URL=

EXPOSE 8081

# Warm the bundle once at startup so the first Expo Go open isn't slow.
CMD ["sh", "-c", "(sleep 15; node -e \"fetch('http://127.0.0.1:'+process.env.PORT+'/index.ts.bundle?platform=android&dev=false&hot=false&lazy=true&minify=true&transform.engine=hermes&transform.bytecode=1&transform.routerRoot=app&unstable_transformProfile=hermes-stable').catch(()=>{})\") & exec npx expo start --port $PORT --no-dev --minify"]
