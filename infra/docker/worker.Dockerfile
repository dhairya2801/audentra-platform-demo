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
RUN npm --workspace @vv/worker run build

FROM node:22.14.0-alpine AS runtime
ENV NODE_ENV=production
WORKDIR /workspace
COPY --from=build --chown=node:node /workspace /workspace
USER node
EXPOSE 3002
CMD ["node", "apps/worker/dist/index.js"]
