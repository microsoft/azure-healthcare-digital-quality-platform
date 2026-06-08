# Build stage
FROM node:slim AS ui
WORKDIR /app

# Copy package files and install dependencies
COPY package*.json ./
RUN npm ci

# Copy the rest of your application's source code
COPY . .

# Build the project
RUN npm run build

FROM nginx:alpine

# ensure entrypoint-scripts dir exists
RUN mkdir -p /docker-entrypoint.d

# Copy built assets into default nginx dir
WORKDIR /usr/share/nginx/html
COPY --from=ui /app/dist .

# copy in your nginx config (replaces the default server block)
COPY ./nginx/nginx.conf /etc/nginx/conf.d/default.conf

# copy & fix env.sh, then make it executable
COPY env.sh /docker-entrypoint.d/env.sh
RUN apk add --no-cache dos2unix \
 && dos2unix /docker-entrypoint.d/env.sh \
 && chmod +x /docker-entrypoint.d/env.sh

EXPOSE 80
