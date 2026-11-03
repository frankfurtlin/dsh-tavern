FROM node:22.21.1-alpine3.22
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY src ./src
USER node
ENV HOST=0.0.0.0 PORT=3010
EXPOSE 3010
CMD ["node", "src/server.mjs"]
