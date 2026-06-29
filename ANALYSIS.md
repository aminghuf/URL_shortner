# URL Shortener — Project Analysis

> Two perspectives: **Simplification** (what to cut/merge) and **OS Course Exam** (what an examiner would test).

---

## Part 1: Simplification — What to Cut, Merge, or Fix

After the K8s migration, the project has accumulated dead weight. Here's what to clean up:

### 1.1 Dead Code in app.py

#### ❌ `import time` (line 7) — unused import

The old in-memory rate limiter used `time.time()` for sliding-window timestamps. The new Redis-based limiter uses `r.incr()` + `r.expire()` — no Python `time` calls remain. The only other occurrence of "time" in the file is a comment ("Populate cache for next time").

**Fix:** Delete `import time`.

#### ❌ `_validate_and_prepare_url()` (line 342) — defined but never called

This function pre-dates the `ThreadPoolExecutor` approach. It was replaced by `_process_urls_chunk()` which also handles intra-batch deduplication and chunk-level tracking. Nobody calls `_validate_and_prepare_url` anymore.

**Fix:** Delete the function.

#### ✅ Rate limiter — already fixed by the migration

The old `_rate_store` dict, `_clean_rate_store()`, and the sliding-window logic are gone. Good.

#### 🐛 `stats` bug (line ~335) — cutoff is now, not now - 24h

```python
cutoff = datetime.now(timezone.utc)
recent_clicks = Click.query.filter(Click.timestamp >= cutoff, ...).count()
```

This checks for clicks *from now to the future* — always zero. Should be:

```python
from datetime import timedelta
cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
```

### 1.2 The `scripts/` Directory — 4 files for the OLD deployment model

| File | Status | Reason |
|------|--------|--------|
| `deploy.sh` | 🗑️ **Remove** | VPS SSH deploy — replaced by `kubectl apply -f k8s/` |
| `vps-setup.sh` | 🗑️ **Remove** | First-time VPS bootstrap — irrelevant in K8s |
| `webhook_server.py` | 🗑️ **Remove** | Runs as root with Docker socket — replaced by GitHub Actions → kubectl |
| `urlshortener-webhook.service` | 🗑️ **Remove** | Systemd unit for the webhook — not needed |

These are 4 files, 285 lines total, maintained for a deployment model you're no longer using. Deleting them removes technical debt and makes the project 51% smaller by file count.

### 1.3 The `nginx/` Directory — Simplified by K8s Ingress

The Ingress controller replaces:

| Component | Replaced by |
|-----------|-------------|
| Custom `nginx/Dockerfile` (23 lines) | Built-in nginx-ingress controller |
| `nginx/nginx.conf` (182 lines) | `k8s/ingress.yaml` + controller annotations |
| SSL cert management | `kubectl create secret tls tls-secret --cert=... --key=...` |
| Rate limiting in Nginx | `nginx.ingress.kubernetes.io/limit-rps: "30"` annotation |

**Keep `nginx/` for local Docker Compose dev** (so you can test the full stack without K8s). The nginx.conf upstream name is already updated to `app-service:8000` which also works in Docker Compose if you set the container name accordingly.

### 1.4 `docker-compose.prod.yml` — No Longer Needed

This file was the production deployment config for the VPS (pulls from Docker Hub). With K8s, the `k8s/app.yaml` pulls the same image and manages deployment. 

**Keep `docker-compose.yml`** for local development (it builds from source, which is faster for iteration). **Delete `docker-compose.prod.yml`** — it's dead weight.

### 1.5 Simplified Project Structure

**Before (22 files):**

```
URL_shortner/
├── app.py             # 514 lines
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── docker-compose.prod.yml   ← DELETE: replaced by k8s/
├── README.md
├── nginx/
│   ├── Dockerfile             # Keep for local dev
│   ├── nginx.conf
│   └── ssl/                   # Placeholders — keep for dev
├── templates/
├── static/
├── tests/
└── scripts/                   ← DELETE: replaced by k8s/
    ├── deploy.sh
    ├── vps-setup.sh
    ├── webhook_server.py
    └── urlshortener-webhook.service
```

**After (16 files):**

```
URL_shortner/
├── app.py             # ~510 lines after cleanup
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── README.md
├── nginx/              # Local dev only
│   ├── Dockerfile
│   └── nginx.conf
├── templates/
├── static/
├── tests/
└── k8s/                # NEW: production deployment
    ├── secret.yaml
    ├── postgres-pvc.yaml
    ├── postgres.yaml
    ├── redis.yaml
    ├── app.yaml
    └── ingress.yaml
```

---

## Part 2: OS Course Exam — What an Examiner Would Ask

Below is organised by **OS textbook topics**, not by file. Each section lists the question first, then the expected answer.

---

### 2.1 Process Management

**Q1:** *Why does the app use Gunicorn with 4 workers and 2 threads? What does each worker represent at the OS level?*

**Expected answer:** Each Gunicorn worker is a separate **process** (forked from the master). With 4 workers, you get 4 OS processes. Each process has 2 threads, for 8 total execution contexts. The OS scheduler treats each process independently — they can run on different CPU cores. Threads share the same memory space (heap, global variables) but have separate stacks. The Gunicorn master process monitors workers and replaces them if they crash.

**Relevant OS concepts:** Process vs thread, fork(), copy-on-write, context switching, process isolation.

**Q2:** *What happens if a Gunicorn worker consumes more than 512 MB of memory?*

**Expected answer:** The container's memory limit (set in k8s/app.yaml and docker-compose.yml) will trigger an **OOM kill** from the kernel (cgroup out-of-memory killer). The entire container (all 4 workers) gets killed — not just the offending worker. Kubernetes will restart the pod based on the liveness probe. The solution is to set per-worker memory limits via Gunicorn's `--max-requests` and `--worker-memory-limit` flags.

**Q3:** *The old rate limiter used an in-memory dict. What OS mechanism made it unsafe for horizontal scaling?*

**Expected answer:** Each process has its own **virtual address space**. The Python dict `_rate_store` lives in the heap of process A. Process B on another pod has an entirely separate virtual address space — it cannot see process A's memory. Only shared memory (via Redis, which lives in its own process) or inter-process communication (IPC) can cross process boundaries. The same problem would occur if you scaled Gunicorn workers on a single machine without sharing the dict.

---

### 2.2 Memory Management

**Q4:** *The k8s manifests define requests vs limits for containers. Explain the difference and what happens when a container exceeds each.*

**Expected answer:** 
- **Requests** = minimum guaranteed memory. The K8s scheduler uses this to pick a node with enough free memory. 
- **Limits** = hard ceiling. If the container exceeds this, the kernel's **OOM killer** terminates it (or throttles it for CPU).
- The `limits.cpus = "0.5"` in docker-compose.yml means the container gets at most 500ms of CPU time per second (CFS quota in the Linux kernel).

**Relevant OS concepts:** Cgroups (control groups), OOM killer, memory overcommit, CFS bandwidth control.

**Q5:** *PostgreSQL has a PersistentVolumeClaim. Explain how the OS delivers persistent storage to the container.*

**Expected answer:** The PVC requests a PersistentVolume from the cluster. In Minikube, this is typically a **hostPath** volume — a directory on the node's filesystem mounted into the container via **bind mount**. The OS sees the mount namespace of the container: `/var/lib/postgresql/data` appears as a local directory but actually points to a directory on the host. Without this, writing to the container's writable layer (overlayfs) would lose data on restart.

---

### 2.3 Concurrency

**Q6:** *The bulk import uses ThreadPoolExecutor. Why is this better than spawning OS processes? Why is Flask able to handle this without breaking?*

**Expected answer:** `ThreadPoolExecutor` creates **user-level threads** (Python threads, backed by OS threads via the GIL). They share the same memory space, so there's no need for IPC or serialization when collecting results. Forking OS processes would require pickle/multiprocessing.Queue to pass data back.

Flask's dev server is single-threaded, but the code runs inside Gunicorn with `--threads 2`, which uses a thread pool to handle concurrent requests. The bulk import's ThreadPoolExecutor is separate — it creates its own worker threads for CSV processing, not for request handling.

**Relevant OS concepts:** Kernel threads vs user threads, GIL (Global Interpreter Lock), process spawning overhead, I/O vs CPU-bound workloads.

**Q7:** *Could you replace ThreadPoolExecutor with multiprocessing.Pool? What would be the trade-off?*

**Expected answer:** Yes. Each worker would be a separate OS process. Benefit: truly parallel execution (no GIL contention) on CPU-bound tasks like regex validation. Cost: higher memory overhead (each process duplicates the Python interpreter), slower startup (fork overhead), and you must serialize data back to the parent via pickle/multiprocessing.Queue. For I/O-bound tasks (waiting on DB inserts), threads are better. Since bulk import is a mix of CPU (regex) and I/O (DB), threads are a reasonable default and simpler.

---

### 2.4 I/O and Blocking

**Q8:** *Why does the Dockerfile set `PYTHONUNBUFFERED=1`?*

**Expected answer:** By default, Python buffers stdout/stderr when not connected to a TTY. `PYTHONUNBUFFERED=1` disables this buffering. In Docker, stdout is captured by the container runtime (dockerd → journald → kubectl logs). Without unbuffered output, log messages would be delayed in the user-space buffer and could be lost during a crash. This is an I/O concern: the trade-off is performance (more frequent write syscalls) vs reliability.

**Relevant OS concepts:** stdio buffering, write() syscall, pipe buffers, log aggregation.

**Q9:** *The application uses both sync (Gunicorn sync workers) and async patterns (ThreadPoolExecutor for bulk import). Is the app I/O-bound or CPU-bound?*

**Expected answer:** Mostly **I/O-bound**. The hot path (redirect) involves:
- Redis get (network I/O)
- PostgreSQL query (network I/O)
- PostgreSQL insert for click tracking (network I/O)

The bulk import is the only CPU-intensive path (regex validation on hundreds of URLs), which is why it uses a ThreadPoolExecutor to parallelize across cores. The redirect path is fast enough with sync workers because it spends most time waiting on network.

---

### 2.5 Networking and Virtualization

**Q10:** *Explain the network path of a redirect request through the K8s architecture.*

**Expected answer:**
```
Browser → Ingress Controller (host port 80/443) 
        → app-service (ClusterIP, port 8000) 
        → any app pod (container port 8000)
        → Redis or PostgreSQL via service DNS
```

Each hop involves:
1. **Ingress**: host network → K8s Service via iptables/IPVS rules
2. **ClusterIP Service**: round-robin load balancing across pod IPs (kernel-level NAT)
3. **Pod-to-Pod communication**: flat overlay network (CNI plugin, e.g., Calico/Flannel)
4. **Service DNS**: CoreDNS resolves `redis-service` to its ClusterIP

**Relevant OS concepts:** Network namespaces, veth pairs, iptables NAT, overlay networks, socket programming.

**Q11:** *What's the difference between Docker Compose networking and Kubernetes networking for this app?*

| Aspect | Docker Compose | Kubernetes |
|--------|---------------|------------|
| DNS resolution | Container names (`redis:6379`) | Service names (`redis-service:6379`) |
| Service discovery | Compose creates a bridge network | CoreDNS + Service objects |
| Load balancing | Not built-in (single container) | kube-proxy (iptables/IPVS) |
| External access | Port mapping (`ports: - "80:80"`) | Ingress + Service (NodePort/LB) |

---

### 2.6 Distributed Systems (Bonus for the Course)

**Q12:** *What is the single point of failure in this architecture after the K8s migration?*

**Expected answer:** **PostgreSQL** has 1 replica. If that pod crashes, the app still works for cache hits (Redis) but fails for new shorten requests and cache misses. **Redis** also has 1 replica — if it crashes, rate limiting breaks (fail-open) and redirects hit the DB directly (slower but still works). The app layer (2 replicas) is the only horizontally-scaled component.

**To fix:** PostgreSQL needs a primary-replica setup (Streaming Replication + pgpool/pgbouncer). Redis needs a replica or cluster mode. This is a standard trade-off: durability/correctness (PostgreSQL) vs speed/cache (Redis).

**Q13:** *Why is the new rate limiter safe with multiple app replicas when the old one wasn't?*

**Expected answer:** The new rate limiter uses Redis `INCR` + `EXPIRE` — an **atomic operation** in a centralized key-value store. All app pods talk to the same Redis instance via `redis-service:6379`. The `INCR` command is atomic at the OS level (single-threaded Redis event loop), so two pods racing for the same IP will get sequential, correct counts. The old Python dict lived in one process's virtual address space — invisible to all other processes.

---

## Part 3: Concrete Improvement List (by Priority)

| # | Change | Effort | Impact |
|---|--------|--------|--------|
| 1 | 🐛 Fix the `stats` time bug (now → now - 24h) | 1 line | Correct analytics |
| 2 | 🗑️ Delete `import time`, unused function, scripts/ | 5 min | Cleaner code, -285 lines |
| 3 | 🗑️ Delete `docker-compose.prod.yml` | 30 sec | Remove dead config |
| 4 | 📦 Add HorizontalPodAutoscaler for the app | 20 min | Auto-scaling demo |
| 5 | 🧪 Add tests: bulk import, rate limit, duplicate, 404 | 1 hr | 4 tests → 12+ tests |
| 6 | 🔧 Fix `DATABASE_URL` env-var substitution in k8s/app.yaml | 2 min | Secrets work correctly |
| 7 | 📝 Create CI/CD using `kubectl apply -f k8s/` | 30 min | Automated deploy to K8s |
| 8 | 🚀 PostgreSQL StatefulSet + streaming replica | 2 hr | HA database |
| 9 | 📊 Add Prometheus metrics + Grafana dashboard | 1-2 hr | Observability |
| 10 | 🧪 Run on Minikube and document the steps | 30 min | "It works" proof |

---

## Summary

**What to simplify:**
- Delete `scripts/` (4 files) — replaced by K8s
- Delete `docker-compose.prod.yml` — replaced by `k8s/`
- Delete `import time` and `_validate_and_prepare_url()` — dead code
- Fix the `stats` timezone bug
- Keep `nginx/` only for local Docker Compose dev

**What an OS examiner cares about:**
- **Processes** (Gunicorn workers, ThreadPoolExecutor, OOM killing)
- **Memory** (virtual address spaces, cgroup limits, PVC storage)
- **Concurrency** (threads vs processes, GIL, atomic Redis INCR)
- **I/O** (buffering, blocking vs non-blocking, network latency)
- **Networking** (veth pairs, iptables, DNS resolution, overlay networks)
- **Virtualization** (containers, cgroup isolation, namespaces, vs VMs)
- **Distributed OS** (shared-nothing, cache coherence, SPOFs)
