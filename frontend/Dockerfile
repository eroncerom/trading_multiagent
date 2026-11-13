FROM node:20-alpine

WORKDIR /app

# Instalar dependencias aprovechando la caché de Docker
COPY package.json package-lock.json ./
RUN npm ci

# Copiar el código fuente
COPY . .

# Exponer puerto de Next.js
EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["npm", "run", "dev"]
