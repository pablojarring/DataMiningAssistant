FROM node:22-alpine

WORKDIR /app

# Copiamos tambien el lockfile y usamos `npm ci`: instala exactamente las
# versiones fijadas, igual que en CI. Con `npm install` y sin lock, cada build
# podria resolver versiones distintas.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]
