FROM node:22.22.2-alpine

ENV NODE_ENV=production
WORKDIR /app/express-api

COPY express-api/package.json express-api/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY common /app/common
COPY express-api/server.js ./

EXPOSE 3000
CMD ["node", "--max-old-space-size=384", "server.js"]