# Web app: Vite dev server and TypeScript checks. Dependencies are installed into the image; Compose
# shadows web/node_modules with an anonymous volume filled from this image.
FROM node:22-bookworm-slim

WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund

CMD ["npm", "run", "dev"]
