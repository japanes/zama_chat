# The admin (Nuxt, no SSR). Two steps: `build` has the packages and makes the build —
# dev runs `nuxt dev` on it with the code mounted; the last one is what a server runs:
# only the build and a small server that gives the pages and passes /api/** on to the
# backend (NUXT_BACKEND_URL).
FROM node:24-bookworm-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

EXPOSE 3120
CMD ["npx", "nuxt", "dev", "--host", "0.0.0.0"]


FROM node:24-bookworm-slim

WORKDIR /app
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3120

COPY --from=build /app/.output ./.output

USER node
EXPOSE 3120
CMD ["node", ".output/server/index.mjs"]
