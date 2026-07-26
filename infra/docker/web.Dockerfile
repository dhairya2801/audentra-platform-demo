FROM node:22.14.0-alpine AS dependencies
WORKDIR /workspace
COPY apps/web/package.json apps/web/package-lock.json apps/web/
COPY packages/contracts packages/contracts
# Keep the deploy image scoped to the web application's graph. The full
# workspace install is appropriate for CI, but unnecessarily resolves the API
# and worker trees on a small preview VM.
RUN npm ci \
      --prefix apps/web \
      --include=dev \
      --workspaces=false \
      --no-audit \
      --no-fund

FROM dependencies AS build
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:4000
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL}
COPY . .
RUN npm --workspace @vv/web run build

FROM node:22.14.0-alpine AS runtime
ENV NODE_ENV=production
ENV PORT=3000
ENV HOST=0.0.0.0
WORKDIR /workspace
COPY --from=build --chown=node:node /workspace/apps/web /workspace/apps/web
COPY --from=build --chown=node:node /workspace/packages/contracts /workspace/packages/contracts
USER node
EXPOSE 3000
CMD ["npm", "--prefix", "apps/web", "run", "start"]
