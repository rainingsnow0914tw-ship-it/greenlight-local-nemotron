# Setup — run Greenlight yourself

Three ways in, from cheapest to most complete. Every step below was run on a fresh
checkout; nothing assumes files outside this repository.

| Path | Time | Cost | You get |
|---|---|---|---|
| **A. Just look** | 0 min | $0 | The public read-only demo with real data |
| **B. Run locally** | ~15 min | $0 until you press a generate button | Full product on `localhost`, planning stack works offline |
| **C. Deploy to your own Google Cloud** | ~45 min | Cloud Run + storage pennies; generation at the prices in `config/pricing.yaml` | The same thing we run |

---

## A. Just look

https://greenlight-demo-309793842076.us-central1.run.app

Same container image as production, with `GREENLIGHT_DEMO_MODE=true`: every read works
(project state, media, ClickHouse raw rows, learned notes), every write or spend returns
HTTP 402 with an explanation. You cannot break anything or spend anything.

---

## B. Run locally

### Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.13 | backend (`python:3.13-slim` is what the container uses) |
| Node | 20+ | frontend build / dev server |
| gcloud CLI | any recent | Application Default Credentials for Vertex AI |
| ffmpeg | any recent, on `PATH` | only for assembly, subtitles, audio mix — everything before that works without it |
| Docker | optional | only if you want the ClickHouse learning ledger locally |

A Google Cloud project with the **Vertex AI API** enabled. The planning stack
(parse → segment → storyboard → estimate) calls Gemini; image/video generation calls
Imagen and Omni/Veo. Billing must be enabled on the project for those calls.

### 1. Clone and install

```bash
git clone https://github.com/rainingsnow0914tw-ship-it/greenlight.git
cd greenlight
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### 2. Credentials and environment

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
cp .env.example .env
```

Edit `.env` — only one line is mandatory:

```
GCP_PROJECT_ID=YOUR_PROJECT_ID
```

Leave `GCS_BUCKET` empty: media is written under `outputs/` on disk. Leave
`GREENLIGHT_STORE` unset: project state is written as local files. Both switch to
GCS / Firestore the moment you set them (see part C).

### 3. Prove the planning stack without spending anything

```bash
python scripts/demo_plan.py
```

Runs config → schema → success prediction → cost estimate end to end with no network
calls. If this prints a plan table, the Python side is wired correctly.

### 4. (Optional) ClickHouse learning ledger

```bash
cd deployment/clickhouse && docker compose up -d && cd ../..
python scripts/setup_clickhouse.py        # applies schema.sql through the app's own connection path
```

`.env.example` already points at this container (`localhost:8123`, user `greenlight`,
password `greenlight_dev`, db `greenlight`). Without ClickHouse the app still runs:
the planner falls back to the priors in `config/priors.yaml` and the UI labels every
estimate as *priors, not measured*.

With ClickHouse up you can run the full $0 wiring suite (it creates and drops a
throwaway test database, hence the flag):

```bash
CLICKHOUSE_ALLOW_DROP=true python scripts/verify_wiring.py
```

Expected: `通過 159 項，失敗 0 項` — 159 checks through the real HTTP routes with the
generation calls stubbed ($0).

### 5. Start it

Two terminals:

```bash
cd backend && uvicorn app.main:app --port 8020
```

```bash
cd frontend && npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` to the backend on 8020.

### 6. First run, with costs stated before each step

1. Drop a screenplay (`.txt`, `.md`, `.docx`, `.pdf`, `.fountain`). Parsing + segmentation
   ≈ $0.15 for a feature-length script.
2. Open a segment → **Storyboard** ≈ $0.05. The estimate card shows cost and predicted
   pass rate per tier before anything else is spent.
3. **Casting** ≈ $0.065 per round (three candidates). Lock one per character.
4. **Prompts** — $0, assembled in code; export them if you want to take them elsewhere.
5. **Test shot** — one shot at the price on the button (Omni Flash ≈ $1 per 10 s).
   Everything before this step is planning; this is the first real video spend.

---

## C. Deploy to your own Google Cloud

The deployed service is one container: Stage 1 of the `Dockerfile` builds the React
frontend, Stage 2 serves it from FastAPI. One URL, one deploy.

### 1. Enable APIs and create storage

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com aiplatform.googleapis.com \
  storage.googleapis.com firestore.googleapis.com secretmanager.googleapis.com

gcloud storage buckets create gs://YOUR_MEDIA_BUCKET --location=us-central1
gcloud firestore databases create --location=us-central1     # Native mode, default database
```

### 2. ClickHouse (optional but it is the learning loop)

Either a ClickHouse Cloud service, or a self-hosted container. What we run: a small
Compute Engine VM (`e2-small`) with `deployment/clickhouse/docker-compose.yml`, HTTP
port 8123 open **only inside the VPC**, and Cloud Run reaching it over Direct VPC egress
(the `--network/--subnet/--vpc-egress` flags below). Apply the schema once from any
machine that can reach it:

```bash
python scripts/setup_clickhouse.py
```

Put the password in Secret Manager; the service reads it from there, never from a file:

```bash
printf '%s' 'YOUR_CLICKHOUSE_PASSWORD' | gcloud secrets create clickhouse-password --data-file=-
```

### 3. Environment file

```bash
cp deployment/env.cloudrun.example.yaml deployment/env.cloudrun.yaml
```

Fill in project id, bucket, ClickHouse host. The file is gitignored.

### 4. Permissions

Cloud Run's runtime service account (the default compute SA unless you pick another)
needs: `roles/aiplatform.user`, `roles/storage.objectAdmin` on the bucket,
`roles/datastore.user`, `roles/secretmanager.secretAccessor`, and
`roles/iam.serviceAccountTokenCreator` **on itself** (media is served through signed
URLs, which requires `signBlob`). On a fresh project the default compute SA already has
most of these via Editor; add the last two explicitly:

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding clickhouse-password --member="serviceAccount:${SA}" --role=roles/secretmanager.secretAccessor
gcloud iam service-accounts add-iam-policy-binding "${SA}" --member="serviceAccount:${SA}" --role=roles/iam.serviceAccountTokenCreator
```

### 5. Deploy

```bash
gcloud run deploy greenlight-api --source . --region us-central1 \
  --env-vars-file deployment/env.cloudrun.yaml \
  --set-secrets CLICKHOUSE_PASSWORD=clickhouse-password:latest \
  --memory 2Gi --cpu 2 --no-cpu-throttling --timeout 900 \
  --network=default --subnet=default --vpc-egress=private-ranges-only \
  --allow-unauthenticated
```

Notes that matter:

- **2 GiB / 2 vCPU** — ffmpeg assembly on 512 MiB OOMs. `--no-cpu-throttling` keeps the
  CPU on for work that happens after the response (ledger flushes).
- **`--timeout 900`** — the route budget is 780 s so a failure is recorded before the
  platform cuts the request.
- **The three VPC flags** are only needed for a private-IP ClickHouse. Drop them for
  ClickHouse Cloud. Leaving them out by mistake does not fail the deploy — the ledger
  silently degrades to priors, which `/api/health` will show as `clickhouse: degraded`.
- `--source .` runs Cloud Build; the first build takes ~5 minutes (fonts and ffmpeg are
  installed in the image for subtitle burn-in).

### 6. Verify

```bash
GREENLIGHT_URL=https://YOUR-SERVICE-URL python scripts/health_check.py
```

25 live checks, $0: health, config, pricing, fonts usable, ClickHouse connected (and the
`endpoint` field shows which host it actually reached), learning overview, demo guard.

### 7. (Optional) a read-only public demo

Second service, same image, one extra variable:

```bash
IMAGE=$(gcloud run services describe greenlight-api --region us-central1 --format='value(spec.template.spec.containers[0].image)')
cp deployment/env.cloudrun.yaml deployment/env.demo.yaml   # then add: GREENLIGHT_DEMO_MODE: 'true'
gcloud run deploy greenlight-demo --image "$IMAGE" --region us-central1 \
  --env-vars-file deployment/env.demo.yaml \
  --set-secrets CLICKHOUSE_PASSWORD=clickhouse-password:latest \
  --memory 2Gi --cpu 2 --network=default --subnet=default --vpc-egress=private-ranges-only \
  --allow-unauthenticated
```

Deploy by image digest, not by rebuilding — the point of the demo is that it is
byte-for-byte the production image. In demo mode a middleware returns 402 for every
non-GET request and the three spend guards (`genai_client`, `video_gen`, `media_store`)
refuse independently, so the guarantee does not depend on any single route remembering
to check.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/api/health` says `clickhouse: degraded` on Cloud Run | service cannot reach the host | private IP → add the three VPC flags; ClickHouse Cloud → check TLS port 8443 and password secret |
| Subtitles burn "successfully" but the video has no text | fonts/fontconfig missing in the image | use the repo `Dockerfile` unchanged; `/api/health` → `fonts.usable` must be `true` |
| Media downloads 500 on Cloud Run | runtime SA cannot `signBlob` | grant `roles/iam.serviceAccountTokenCreator` on the SA to itself |
| Local video playback works but Cloud Run returns 502 on large files | responses over 32 MiB | signed URLs handle this in production; locally the server streams the file |
| `401 UNAUTHENTICATED` mid-batch | cached token expired | handled: the client refreshes on 401; if it persists, re-run `gcloud auth application-default login` |
| Windows: code changes have no effect | orphaned dev server still holding the port | change the backend port in `vite.config.ts` and the uvicorn command together |
