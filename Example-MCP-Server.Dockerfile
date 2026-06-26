FROM node:20

WORKDIR /app

ARG CUSTOM_CA_CERT=custom-ca.crt
COPY ${CUSTOM_CA_CERT} /usr/local/share/ca-certificates/custom-ca.crt
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && update-ca-certificates \
  && rm -rf /var/lib/apt/lists/*
RUN npm config set cafile /usr/local/share/ca-certificates/custom-ca.crt

COPY package*.json ./
RUN npm install --include=dev

COPY . .

RUN npm run build

EXPOSE 3001

CMD ["node","dist/index.js"]
