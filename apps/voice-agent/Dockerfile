FROM node:22.19.0-bookworm-slim AS build

WORKDIR /workspace

COPY package.json package-lock.json tsconfig.base.json ./
COPY apps/voice-agent/package.json apps/voice-agent/tsconfig.json ./apps/voice-agent/
COPY packages/contracts/package.json packages/contracts/tsconfig.json ./packages/contracts/

RUN npm ci --workspace @vv/voice-agent --include-workspace-root=false

COPY apps/voice-agent/src ./apps/voice-agent/src
COPY packages/contracts/src ./packages/contracts/src

RUN npm --workspace @vv/voice-agent run build
RUN npm prune --omit=dev --workspace @vv/voice-agent --include-workspace-root=false

FROM node:22.19.0-bookworm-slim AS runtime

ENV NODE_ENV=production
WORKDIR /workspace

COPY --from=build /workspace/node_modules ./node_modules
COPY --from=build /workspace/apps/voice-agent/node_modules ./apps/voice-agent/node_modules
COPY --from=build /workspace/apps/voice-agent/package.json ./apps/voice-agent/package.json
COPY --from=build /workspace/apps/voice-agent/dist ./apps/voice-agent/dist
COPY --from=build /workspace/packages/contracts ./packages/contracts

USER node

CMD ["node", "apps/voice-agent/dist/index.js", "start"]
