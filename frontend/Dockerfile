# ============================================================================
# Frontend image — React + TypeScript (Vite).
# `dev` target = Vite dev server (HMR); `prod` = static build served by nginx.
# ============================================================================

# ---- deps ------------------------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# `npm install` (not `npm ci`) since no lockfile is committed; it resolves and
# writes one into the image. Commit the generated package-lock.json to pin.
RUN npm install

# ---- dev -------------------------------------------------------------------
FROM node:22-alpine AS dev
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]

# ---- build -----------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- prod (static assets via nginx) ----------------------------------------
FROM nginx:1.27-alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
