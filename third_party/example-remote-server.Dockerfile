FROM node:22-alpine AS builder

WORKDIR /app

ARG CUSTOM_CA_CERT=custom-ca.crt
COPY ${CUSTOM_CA_CERT} /usr/local/share/ca-certificates/custom-ca.crt
RUN apk add --no-cache ca-certificates && update-ca-certificates
RUN npm config set cafile /usr/local/share/ca-certificates/custom-ca.crt

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY . .
RUN npm run build

FROM node:22-alpine

WORKDIR /app

ARG CUSTOM_CA_CERT=custom-ca.crt
COPY ${CUSTOM_CA_CERT} /usr/local/share/ca-certificates/custom-ca.crt
RUN apk add --no-cache ca-certificates && update-ca-certificates
RUN npm config set cafile /usr/local/share/ca-certificates/custom-ca.crt

COPY --from=builder /app/dist /app/dist
COPY --from=builder /app/package.json /app/package.json
COPY --from=builder /app/package-lock.json /app/package-lock.json
RUN --mount=type=cache,target=/root/.npm npm ci --ignore-scripts --omit-dev

ENV AUTH_MODE=internal
ENV PORT=3232
ENV BASE_URI=http://localhost:3232
ENV NODE_ENV=development

EXPOSE 3232

CMD ["node", "dist/index.js"]
