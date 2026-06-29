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

---

## Kubernetes Setup (k3s on a VPS)

### 1. Install k3s

```bash
curl -sfL https://get.k3s.io | sh -
kubectl get nodes   # verify cluster is up
```

### 2. Create the TLS secret

```bash
kubectl create secret tls tls-secret \
  --cert=/etc/letsencrypt/live/aminghuf.dev/fullchain.pem \
  --key=/etc/letsencrypt/live/aminghuf.dev/privkey.pem
```

### 3. Deploy everything

```bash
git clone https://github.com/aminghuf/URL_shortner.git
cd URL_shortner
kubectl apply -f k8s/monitoring/namespace.yaml
kubectl apply -R -f k8s/ --validate=false
```

### 4. Install metrics-server (required for HPA)

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system --type='json' \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

### 5. Verify

```bash
kubectl get pods           # all pods running
kubectl get hpa            # HPA showing CPU %
kubectl top nodes          # metrics-server working
```

---

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

---

## Monitoring

Grafana is accessible at `http://<vps-ip>:32000` (login: `admin` / `admin`).

Prometheus scrapes:
- **kube-state-metrics** — pod counts, replica status, deployment health
- **node-exporter** — VPS CPU, RAM, disk, network

**Recommended dashboards to import** (Grafana → Dashboards → Import):

| ID | Shows |
|----|-------|
| `1860` | Node Exporter Full — CPU, RAM, disk, network |
| `13332` | Kubernetes cluster — pod counts, replica status |

---

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI |
| `GET` | `/api/health` | Health check (DB + Redis status) |
| `POST` | `/shorten` | Create short URL — body: `{"url": "https://..."}` |
| `GET` | `/<short_code>` | Redirect to original URL |
| `GET` | `/stats/<short_code>` | Click statistics |
| `POST` | `/bulk-import` | Bulk import from CSV/text file |

---

## Local Development

```bash
# Start all services locally with Docker Compose
docker compose up --build

# Run tests
pytest -v
```

---

## Authors

- **amin** — [@aminghuf](https://github.com/aminghuf)
- **shakibofski** — Frontend development
