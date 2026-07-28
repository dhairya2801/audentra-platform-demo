FROM node:22.14.0-alpine AS dependencies
WORKDIR /workspace
COPY package.json package-lock.json* ./
COPY apps/api/package.json apps/api/package.json
COPY apps/worker/package.json apps/worker/package.json
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/document-preprocessing/package.json packages/document-preprocessing/package.json
RUN if [ -f package-lock.json ]; then \
      npm ci --workspaces --include-workspace-root; \
    else \
      npm install --workspaces --include-workspace-root; \
    fi

FROM dependencies AS build
COPY . .
RUN npm --workspace @vv/api run build

FROM node:22.14.0-alpine AS runtime
ENV NODE_ENV=production
WORKDIR /workspace
COPY packages/document-preprocessing/requirements.txt /tmp/document-preprocessing-requirements.txt
RUN apk add --no-cache python3 py3-pip \
  && python3 -m pip install \
    --no-cache-dir \
    --break-system-packages \
    --requirement /tmp/document-preprocessing-requirements.txt
COPY --from=build --chown=node:node /workspace /workspace
USER node
EXPOSE 4000
# The API itself is compiled so Nest decorator metadata is retained. The tsx
# loader is still registered because shared workspace packages intentionally
# export TypeScript source during development.
CMD ["node", "--import", "tsx", "apps/api/dist/main.js"]
