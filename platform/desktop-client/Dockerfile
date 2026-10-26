# Trust Agent — Desktop Client white-label (Next.js 16 App Router, spec 010).
# App del empleado: login OIDC/PKCE del realm de su org + enrolamiento de
# dispositivo + sesión gobernada (:9090). Es también el target del
# "Hermes download link" del Provider Console (spec 014 D-PC-09).
FROM node:22-slim

ENV NEXT_TELEMETRY_DISABLED=1
# NODE_ENV se pone en production SOLO después del build: npm ci con
# NODE_ENV=production se salta las devDependencies (@tailwindcss/turbopack
# es devDependency y next build la necesita).

WORKDIR /app

# Deps primero (capa cacheable) — con devDependencies incluidos.
COPY package.json package-lock.json ./
RUN npm ci --include=dev

# Fuente + build.
COPY . .
RUN npm run build

ENV NODE_ENV=production
EXPOSE 9090
CMD ["npx", "next", "start", "--port", "9090"]
