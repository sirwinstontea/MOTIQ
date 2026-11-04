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

CMD ["sh", "./start.sh"]
