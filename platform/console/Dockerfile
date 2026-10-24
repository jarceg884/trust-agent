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
CMD ["npx", "next", "start", "--port", "3100"]
