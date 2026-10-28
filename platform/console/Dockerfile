# Trust Agent — Admin Console (Next.js 16 App Router, spec 009).
# Contexto de build: platform/console.
FROM node:22-slim

ENV NEXT_TELEMETRY_DISABLED=1
# NODE_ENV se pone en production SOLO después del build: npm ci con
# NODE_ENV=production se salta las devDependencies (@tailwindcss/turbopack
# es devDependency y next build la necesita).

WORKDIR /app

# Deps primero (capa cacheable) — con devDependencies incluidas.
COPY package.json package-lock.json ./
RUN npm ci --include=dev

# Fuente + build.
COPY . .
RUN npm run build

ENV NODE_ENV=production
EXPOSE 3100
# Puerto desde el entorno (t_b62edff2): default dev 3100; el .env lo cambia
# sin rebuild (TRUST_CONSOLE_PORT). Next arranca en 0.0.0.0.
CMD ["sh", "-c", "npx next start --port ${TRUST_CONSOLE_PORT:-3100}"]
