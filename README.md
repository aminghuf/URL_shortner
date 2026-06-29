# URL Shortener — Kubernetes Deployment

A production-style URL shortening service built with Python/Flask, deployed on a k3s Kubernetes cluster. Features auto-scaling, distributed caching, persistent storage, CI/CD, and a live monitoring dashboard.

---

## Architecture

```
                        Internet
                            │
                     ┌──────▼──────┐
                     │   Traefik   │  ← Ingress controller (port 80/443)
                     │  (k3s built-in) │    routes aminghuf.dev
                     └──────┬──────┘
                            │
               ┌────────────▼────────────┐
               │      app-service        │  ← ClusterIP, load-balances pods
               └────────────┬────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
     ┌─────────┐      ┌─────────┐      ┌─────────┐
     │ app pod │      │ app pod │      │ app pod │  ← 2–6 replicas (HPA)
     │ :8000   │      │ :8000   │      │ :8000   │
     └────┬────┘      └────┬────┘      └────┬────┘
          └────────────────┼────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
     ┌────────────────┐       ┌──────────────────┐
     │   PostgreSQL   │       │      Redis        │
     │  (1 pod + PVC) │       │    (1 pod)        │
     │  persistent    │       │  cache + rate     │
     │  storage       │       │  limiting         │
     └────────────────┘       └──────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Application | Python 3.11, Flask 3.x, Gunicorn |
| Database | PostgreSQL 16 (Alpine) + SQLAlchemy ORM |
| Cache / Rate limiting | Redis 7 (Alpine) |
| Container runtime | Docker |
| Orchestration | Kubernetes (k3s) |
| Ingress | Traefik (k3s built-in) |
| CI/CD | GitHub Actions → Docker Hub → kubectl |
| Monitoring | Prometheus + Grafana |
| Auto-scaling | Kubernetes HPA + metrics-server |

---

## Project Structure

```
URL_shortner/
├── app.py                    # Flask application
├── Dockerfile                # App container image
├── docker-compose.yml        # Local development only
├── requirements.txt
│
├── templates/
│   └── index.html
├── static/
│   └── style.css
├── tests/
│   └── test_app.py
│
├── k8s/                      # Kubernetes manifests (applied in order)
│   ├── secret.yaml           # DB password (base64)
│   ├── postgres-pvc.yaml     # Persistent disk for PostgreSQL
│   ├── postgres.yaml         # PostgreSQL Deployment + Service
│   ├── redis.yaml            # Redis Deployment + Service
│   ├── app.yaml              # Flask app Deployment + Service (2–6 replicas)
│   ├── ingress.yaml          # Traefik Ingress (routes aminghuf.dev)
│   ├── hpa.yaml              # HorizontalPodAutoscaler (scales on CPU > 50%)
│   └── monitoring/
│       ├── namespace.yaml    # monitoring namespace
│       ├── prometheus.yaml   # Prometheus Deployment + Service + RBAC
│       ├── grafana.yaml      # Grafana Deployment + NodePort :32000
│       ├── kube-state-metrics.yaml  # K8s object metrics (pods, replicas)
│       └── node-exporter.yaml       # Host metrics (CPU, RAM, disk)
│
└── .github/workflows/
    └── deploy.yml            # CI/CD pipeline
```


## CI/CD Pipeline

Every push to `main` runs two jobs:

**Job 1 — test**
- Installs Python dependencies
- Runs `pytest` (SQLite, no external services needed)

**Job 2 — build-and-deploy** (only if tests pass)
- Builds Docker image and pushes to Docker Hub (`:latest` + `:<commit-sha>`)
- Applies all K8s manifests via `kubectl apply -R -f k8s/`
- Rolls out the new image with `kubectl set image`
- Waits for rollout to complete before marking success

Required GitHub secrets:

| Secret | Value |
|--------|-------|
| `DOCKER_HUB_USERNAME` | Docker Hub username |
| `DOCKER_HUB_TOKEN` | Docker Hub access token |
| `KUBECONFIG` | Contents of `/etc/rancher/k3s/k3s.yaml` (with VPS public IP) |

---

## Auto-scaling (HPA)

The app scales automatically between **2 and 6 replicas** based on CPU usage.

- Scale-up triggers when average CPU across all pods exceeds **50%**
- Scale-down happens after CPU stays low for ~5 minutes

```bash
# Watch HPA react to load in real time
kubectl get hpa -w

# Generate load to trigger scaling
ab -n 1000 -c 50 http://aminghuf.dev/api/health
```


## Local Development

```bash
# Start all services locally with Docker Compose
docker compose up --build

# Run tests
pytest -v
```

---

