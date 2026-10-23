---
name: dockerfile-audit
description: Guía experta para agentes LLM sobre plantillas multi-stage y hardening de Dockerfiles para Java Spring Boot, Ruby, Go, Python MCP, React y NestJS.
---

# Skill: Hardening y Plantillas Multi-Stage de Dockerfiles

## 1. Principio Fundamental
> **Cero ejecución como root en producción. Cero herramientas de compilación en la imagen final.**
> Toda imagen debe utilizar construcción multi-etapa (*multi-stage build*), declarar usuario no privilegiado y fijar versiones de base.

---

## 2. Plantillas Multi-Etapa de Producción por Stack

### 2.1 Java Spring Boot (Multi-Stage Layered JAR)
```dockerfile
# Etapa 1: Builder
FROM eclipse-temurin:21-jdk-alpine AS builder
WORKDIR /workspace
COPY mvnw .
COPY .mvn .mvn
COPY pom.xml .
RUN ./mvnw dependency:go-offline -B
COPY src src
RUN ./mvnw clean package -DskipTests
RUN java -Djarmode=layertools -jar target/*.jar extract

# Etapa 2: Runner final
FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
RUN addgroup -S spring && adduser -S spring -G spring
USER spring:spring

COPY --from=builder /workspace/dependencies/ ./
COPY --from=builder /workspace/spring-boot-loader/ ./
COPY --from=builder /workspace/snapshot-dependencies/ ./
COPY --from=builder /workspace/application/ ./

EXPOSE 8080
ENTRYPOINT ["java", "org.springframework.boot.loader.launch.JarLauncher"]
```

---

### 2.2 Ruby / Rails
```dockerfile
# Etapa 1: Builder
FROM ruby:3.3-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
COPY Gemfile Gemfile.lock ./
RUN bundle config set without 'development test' && bundle install --jobs 4

# Etapa 2: Runner final
FROM ruby:3.3-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
RUN groupadd -r appgroup && useradd -r -g appgroup appuser
COPY --from=builder /usr/local/bundle /usr/local/bundle
COPY . .
RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 3000
CMD ["bundle", "exec", "puma", "-C", "config/puma.rb"]
```

---

### 2.3 Golang (Distroless / Scratch)
```dockerfile
# Etapa 1: Builder
FROM golang:1.22-alpine AS builder
WORKDIR /build
RUN apk add --no-cache git
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-w -s" -o /app-binary .

# Etapa 2: Runner final ultra-minimal
FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /
COPY --from=builder /app-binary /app-binary
USER nonroot:nonroot

EXPOSE 8080
ENTRYPOINT ["/app-binary"]
```

---

### 2.4 Python MCP / FastMCP
```dockerfile
# Etapa 1: Builder
FROM python:3.11-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Etapa 2: Runner final
FROM python:3.11-slim
WORKDIR /app
RUN groupadd -r mcp && useradd -r -g mcp mcpuser
COPY --from=builder /install /usr/local
COPY . .
RUN chown -R mcpuser:mcp /app
USER mcpuser

ENTRYPOINT ["python", "-m", "my_mcp_server"]
```

---

### 2.5 Frontend React (Nginx Alpine)
```dockerfile
# Etapa 1: Builder
FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Etapa 2: Runner con Nginx no root
FROM nginxinc/nginx-unprivileged:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
# Configuración personalizada de Nginx para Single Page Apps (SPA)
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
```

---

### 2.6 NestJS Node.js TypeScript
```dockerfile
# Etapa 1: Builder
FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build
RUN npm prune --production

# Etapa 2: Runner final
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/package*.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/dist ./dist
USER node

EXPOSE 3000
CMD ["node", "dist/main.js"]
```
