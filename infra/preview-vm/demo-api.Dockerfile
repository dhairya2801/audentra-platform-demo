FROM node:22.14.0-alpine AS dependencies
WORKDIR /runtime

# The preview API only has one native runtime dependency. Installing the whole
# monorepo here makes a 1 GB VM resolve the web, API, and worker dependency
# graphs for no benefit and can exhaust its memory.
RUN npm install \
  --omit=dev \
  --no-package-lock \
  --no-audit \
  --no-fund \
  sharp@0.35.3

FROM node:22.14.0-alpine AS runtime
ENV NODE_ENV=production
ENV DEMO_API_PORT=4000
WORKDIR /workspace

COPY packages/document-preprocessing/requirements.txt /tmp/document-preprocessing-requirements.txt
RUN apk add --no-cache python3 py3-pip \
  && python3 -m pip install \
    --no-cache-dir \
    --break-system-packages \
    --requirement /tmp/document-preprocessing-requirements.txt

COPY --from=dependencies /runtime/node_modules /workspace/node_modules
COPY tools/demo-api tools/demo-api
COPY packages/document-preprocessing /workspace/node_modules/@vv/document-preprocessing
COPY apps/web/public/documents/onboarding apps/web/public/documents/onboarding

RUN mkdir -p /var/lib/vv \
  && chown -R node:node /workspace /var/lib/vv

USER node
EXPOSE 4000
CMD ["node", "tools/demo-api/src/server.js"]
