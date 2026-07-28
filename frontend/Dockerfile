# Next.js 상담원 검토 UI. Node >=20.9(next/package.json engines) — LTS 20 사용.
# 멀티스테이지: deps(캐시 레이어 분리) -> build -> runtime(프로덕션 산출물만).
FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# CS_API_KEY는 빌드 시점에 필요 없다(런타임에 route.ts가 process.env로 읽음) —
# next build가 env 존재를 요구하지 않으므로 별도 ARG 없이 진행한다.
RUN npm run build

FROM node:20-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/public ./public
COPY --from=build /app/.next ./.next
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/next.config.ts ./next.config.ts

EXPOSE 3000
CMD ["npm", "start"]
