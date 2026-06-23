# syntax=docker/dockerfile:1
# Two stages: build the React bundle with Node, then serve it from the stdlib
# Python server. This is what makes `python webapp/server.py` ship the *new* UI
# (webapp/static/dist) on a fresh deploy, even though the built bundle isn't in git.

# ---- Stage 1: build webapp/frontend -> webapp/static/dist ----
FROM node:20-slim AS frontend
WORKDIR /app/webapp/frontend
COPY webapp/frontend/package.json webapp/frontend/package-lock.json ./
RUN npm ci
COPY webapp/frontend/ ./
RUN npm run build      # vite outDir "../static/dist" -> /app/webapp/static/dist

# ---- Stage 2: Python runtime (JSON API + prebuilt bundle) ----
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=frontend /app/webapp/static/dist ./webapp/static/dist
EXPOSE 8000
# server.py reads $PORT (Render/Railway inject it) and $HOST from the environment.
CMD ["python", "webapp/server.py"]
