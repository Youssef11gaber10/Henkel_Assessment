# DataHub Governance — Tag Ownerless Datasets

A Kubernetes CronJob that automatically finds all datasets in DataHub with no assigned owners and applies a `needs-owner` tag to them.

---

## What This Does

1. Connects to DataHub GMS (backend API) via GraphQL
2. Fetches all datasets with pagination
3. Filters datasets that have no owners AND are not already tagged
4. Creates the `needs-owner` tag if it doesn't exist
5. Applies the tag to every ownerless dataset
6. Supports `DRY_RUN=true` to preview changes without applying them
7. Is fully idempotent — safe to run multiple times

---

## Repository Structure

```
datahub-governance/
├── script/
│   ├── script.py                   # Main governance script
│   ├── requirements.txt            # Python dependencies
│   └── Dockerfile                  # Multi-stage, non-root container image
├── k8s/
│   ├── 00-namespace.yaml           # datahub namespace
│   ├── 01-configmap.yaml           # Non-sensitive env vars
│   ├── 02-secret.yaml              # DataHub token (template only — do not commit real value)
│   ├── 03-cronjob.yaml             # CronJob that runs the script on a schedule
│   └── sealedsecret.yaml           # Encrypted secret safe to commit to git
├── helm/
│   ├── prerequisites.yaml          # Helm values for Kafka, MySQL, Elasticsearch
│   └── datahub-values.yaml         # Helm values for DataHub + NGINX Ingress
├── sample-data/
│   ├── recipe.yml                  # DataHub ingestion recipe
│   └── sample_metadata.json        # 5 sample datasets (3 without owners)
├── kind-config.yaml                # kind cluster config with port mappings for NGINX
└── README.md
```

---

## Prerequisites

| Tool | How to install |
|------|---------------|
| Docker Desktop | https://www.docker.com/products/docker-desktop |
| kubectl | https://kubernetes.io/docs/tasks/tools/ |
| Helm | https://helm.sh/docs/intro/install/ |
| kind | https://kind.sigs.k8s.io/docs/user/quick-start/ |
| Python 3.11+ | https://www.python.org/downloads/ |
| DataHub CLI | `pip install acryl-datahub` |
| kubeseal | See Step 9 below |

---

## How to Run Locally

### Step 1 — Create the kind cluster with NGINX Ingress support

kind needs port mappings defined at cluster creation time so NGINX can receive traffic from your browser on port 80.

```bash
cat <<EOF | kind create cluster --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  kubeadmConfigPatches:
  - |
    kind: InitConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        node-labels: "ingress-ready=true"
  extraPortMappings:
  - containerPort: 80
    hostPort: 80
    protocol: TCP
  - containerPort: 443
    hostPort: 443
    protocol: TCP
- role: worker
- role: worker
EOF
```

Install the NGINX Ingress Controller (kind-specific manifest):

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/master/deploy/static/provider/kind/deploy.yaml

# Wait until NGINX pod is ready before continuing
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=90s
```

Add the DataHub hostname to your `/etc/hosts` so your browser can resolve it:

```bash
# Linux / Mac — edit with sudo
echo "127.0.0.1 datahub.local" | sudo tee -a /etc/hosts

# Windows — edit C:\Windows\System32\drivers\etc\hosts as Administrator
# Add: 127.0.0.1 datahub.local
```

---

### Step 2 — Create the namespace and required secrets

```bash
kubectl apply -f k8s/00-namespace.yaml

kubectl create secret generic mysql-secrets \
  --from-literal=mysql-root-password=datahub -n datahub

kubectl create secret generic neo4j-secrets \
  --from-literal=neo4j-password=datahub -n datahub
```

---

### Step 3 — Install DataHub prerequisites

This installs Kafka, MySQL, and Elasticsearch — all required before DataHub itself can start.

```bash
helm repo add datahub https://helm.datahubproject.io/
helm repo update

helm install prerequisites datahub/datahub-prerequisites \
  -f helm/prerequisites.yaml -n datahub

# Wait for all pods to show Running or Completed (takes 3-5 min)
kubectl get pods -n datahub -w
# Press Ctrl+C when ready
```

---

### Step 4 — Install DataHub

```bash
helm install datahub datahub/datahub \
  -n datahub \
  -f helm/datahub-values.yaml \
  --timeout 20m

# Wait for all pods to show Running
kubectl get pods -n datahub -w
```

Open **http://datahub.local** in your browser — login: `datahub` / `datahub`

---

### Step 5 — Generate an access token

DataHub requires a Bearer token for API calls.

1. Open http://datahub.local
2. Go to **Settings → Access Tokens**
3. Click **Generate New Token**, give it a name (e.g. `governance-script`)
4. Copy the token — you need it in Steps 6 and 7

---

### Step 6 — Load sample data

Port-forward GMS so the DataHub CLI can reach the backend API:

```bash
# Run this in a dedicated terminal and keep it open
kubectl port-forward svc/datahub-datahub-gms 8080:8080 -n datahub
```

In a new terminal, add your token to the recipe file:

```bash
# Edit sample-data/recipe.yml
# Uncomment the token line and paste your token:
#   token: "eyJhbGci..."
```

Then ingest:

```bash
cd sample-data
datahub ingest -c recipe.yml
```

This loads 5 datasets — 2 with owners assigned, 3 deliberately without. Verify they appear at http://datahub.local by searching for `customers`, `products`, or `sessions`.

---

### Step 7 — Run the script locally to test it

With GMS still port-forwarded from Step 6, run the script directly on your machine to verify it works before containerizing it.

```bash
cd script

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the script (it reads DATAHUB_GMS_URL and DATAHUB_TOKEN from env)
export DATAHUB_GMS_URL="http://localhost:8080"
export DATAHUB_TOKEN="your-token-here"
python script.py
```

Expected output:

```
[INFO] ============================================================
[INFO] DataHub Governance: Tag Ownerless Datasets
[INFO] GMS URL   : http://localhost:8080
[INFO] DRY RUN   : False
[INFO] ============================================================
[INFO] Total datasets found: 5
[INFO] Datasets with owners          : 2
[INFO] Already tagged (needs-owner)  : 0  (skipping — idempotent)
[INFO] Ownerless, not yet tagged     : 3
[INFO] Tagged: customers (urn:li:dataset:...)
[INFO] Tagged: products  (urn:li:dataset:...)
[INFO] Tagged: sessions  (urn:li:dataset:...)
[INFO] Done. Tagged 3 dataset(s).
```

Run it a second time to confirm idempotency — output should show 3 already tagged, 0 to process.

---

### Step 8 — Build and push the Docker image

```bash
cd script

docker build -t <your-dockerhub-username>/cronjob-henkel:latest .
docker push <your-dockerhub-username>/cronjob-henkel:latest
```

Update the `image:` field in `k8s/03-cronjob.yaml` to your pushed image name.

---

### Step 9 — Seal the secret (safe to commit to git)

A plain Kubernetes Secret is just base64 — not encrypted and not safe to commit. Sealed Secrets encrypts it with the cluster's own key so only that cluster can decrypt it.

Install the Sealed Secrets operator inside the cluster:

```bash
kubectl apply -f https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.37.0/controller.yaml
```

Install kubeseal on your machine:

```bash
# Linux
curl -OL "https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.37.0/kubeseal-0.37.0-linux-amd64.tar.gz"
tar -xvzf kubeseal-0.37.0-linux-amd64.tar.gz kubeseal
sudo install -m 755 kubeseal /usr/local/bin/kubeseal

# Mac
brew install kubeseal
```

First, put your real token into `k8s/02-secret.yaml` (this file stays local, never pushed):

```yaml
stringData:
  DATAHUB_TOKEN: "your-real-token-here"
```

Then encrypt it:

```bash
cd k8s/

# Fetch the cluster's public key
kubeseal --fetch-cert > publickey.pem

# Encrypt secret.yaml → sealedsecret.yaml
kubeseal --format=yaml --cert=publickey.pem < 02-secret.yaml > sealedsecret.yaml
```

Add both `publickey.pem` and `02-secret.yaml` to `.gitignore`. Only `sealedsecret.yaml` gets committed.

---

### Step 10 — Deploy the CronJob to the cluster

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-configmap.yaml
kubectl apply -f k8s/sealedsecret.yaml
kubectl apply -f k8s/03-cronjob.yaml
```

Verify everything was created:

```bash
kubectl get configmap cronjob-cm -n datahub
kubectl get sealedsecret cronjob-secret -n datahub
kubectl get cronjob tag-ownerless-cronjob -n datahub
```

Manually trigger a run without waiting for the cron schedule:

```bash
kubectl create job --from=cronjob/tag-ownerless-cronjob manual-test-1 -n datahub

# Watch the logs live
kubectl logs -l job-name=manual-test-1 -n datahub -f
```

Verify the `needs-owner` tag appears on the ownerless datasets at http://datahub.local.

---

## Key Design Decisions and Assumptions

**GraphQL over the REST API**
DataHub has both a REST and a GraphQL API. GraphQL was chosen because it lets you request exactly the fields needed (urn, ownership, tags) in a single call per page, and mutations like `createTag` and `addTag` have typed inputs that catch errors early. The REST API would require separate calls per action and returns more data than needed.

**Talking directly to GMS, not the frontend**
The script points to `datahub-datahub-gms:8080` inside the cluster, not to the frontend at port 9002. The frontend is a React UI — the actual GraphQL API is served by GMS. Talking to GMS directly avoids an unnecessary proxy hop.

**Pagination over a single large query**
DataHub's GraphQL search returns results in pages. The script loops with `start` and `count` until all datasets are fetched. Without this, only the first 100 datasets would ever be processed and the rest silently skipped. Batch size is configurable via `BATCH_SIZE` env var.

**Idempotency at two levels**
Before creating the tag: search for it by name — only call `createTag` if it does not exist. Before tagging a dataset: check its current tags — only call `addTag` if the tag is not already there. This means the CronJob can run on any schedule, including multiple times in a row, without side effects or duplicate entries.

**Sealed Secrets instead of a plain Kubernetes Secret**
A plain Kubernetes Secret is only base64 encoded — anyone with repo access can decode it. Sealed Secrets encrypts the token with an asymmetric key held by an in-cluster controller. The encrypted file (`sealedsecret.yaml`) is safe to push to git. The plain `02-secret.yaml` stays local only.

**`concurrencyPolicy: Forbid` on the CronJob**
If a run takes longer than the schedule interval (unlikely but possible with large datasets), Kubernetes would normally start a second parallel run. `Forbid` prevents this, ensuring only one instance runs at a time and avoiding race conditions where two pods try to tag the same dataset simultaneously.

**Multi-stage Dockerfile with a non-root user**
Stage 1 installs dependencies into a Python virtual environment. Stage 2 copies only the venv and the script — no pip, no build tools, no cache. This keeps the image small. The container runs as a dedicated non-root user (UID 1000), and `readOnlyRootFilesystem: true` is set in the pod security context, since the script does not need to write any files.

**Assumptions made during development**
- DataHub is deployed with `metadata_service_authentication` enabled, so a Bearer token is required for all API calls.
- The Helm release is named `datahub`, which determines the internal service DNS names (`datahub-datahub-gms`, `datahub-datahub-frontend`). A different release name would require updating the ConfigMap.
- Only `DATASET` entity type needs to be governed. Other entity types (charts, dashboards, pipelines) are out of scope for this task.
- Elasticsearch is used as the graph/search backend (not Neo4j), which matches the prerequisites chart default.

---

## Environment Variables

| Variable | Value in cluster | Description |
|----------|-----------------|-------------|
| `DATAHUB_GMS_URL` | `http://datahub-datahub-gms:8080` | Internal K8s service name for GMS |
| `DATAHUB_TOKEN` | *(from sealed secret)* | Bearer token for DataHub authentication |
| `DRY_RUN` | `false` | Set to `true` to preview without making any changes |
| `BATCH_SIZE` | `100` | Number of datasets fetched per GraphQL page |
| `TAG_NAME` | `needs-owner` | Name of the tag to create and apply |
| `TAG_DESCRIPTION` | `Dataset has no assigned owner...` | Description written to the tag entity |

---

## Known Limitations

**Only covers DATASET entity type.**
The script searches for and tags `DATASET` entities only. DataHub also has `CHART`, `DASHBOARD`, `DATA_JOB`, `DATA_FLOW`, and `ML_MODEL` entity types which could also lack owners. Extending to these would require adding each type as a separate search query, since DataHub's GraphQL `search` input does not support multiple types in one call.

**Token expires.**
The access token generated from the UI has an expiry date. When it expires, the CronJob will start failing with 401 errors. There is no automatic token refresh — a new token must be generated, and the sealed secret must be re-encrypted and redeployed. A service account token with no expiry would be more appropriate for production.

**No notification when ownerless datasets are found.**
The script logs what it tags but does not alert anyone. In a real governance workflow, a Slack or Teams message listing the newly tagged datasets would allow data owners to act on them.

**Re-creating the cluster invalidates the sealed secret.**
Sealed Secrets are encrypted with a key that lives inside the cluster. If you delete and recreate the kind cluster, the sealed secret can no longer be decrypted by the new cluster. You would need to re-seal the secret against the new cluster's key.

**Sample data uses a legacy ingestion format.**
`sample_metadata.json` uses the older `DatasetSnapshot` MCP format. DataHub's current preferred format is `MetadataChangeProposalWrapper`. The older format still works but may be deprecated in future versions.

**Dry-run applies cluster-wide.**
`DRY_RUN` is set in the ConfigMap which affects all runs. There is no way to trigger a single dry-run without editing the ConfigMap, reapplying it, running the job, then reverting. A `--dry-run` flag passed as a job argument would be cleaner.

---

## What I Would Improve or Change With More Time

**CI/CD pipeline to automate build and deployment.**
Right now, any change to `script.py` requires manually running `docker build`, `docker push`, and `kubectl apply`. With more time I would add a GitHub Actions pipeline that does this automatically on every push to `main`:

```
Push to main
    │
    ├── 1. Run tests (pytest)
    │
    ├── 2. Build Docker image
    │       docker build -t <registry>/cronjob-henkel:${{ github.sha }} .
    │
    ├── 3. Push image to DockerHub / GHCR
    │       docker push <registry>/cronjob-henkel:${{ github.sha }}
    │
    └── 4. Update the CronJob in the cluster
            kubectl set image cronjob/tag-ownerless-cronjob \
              cronjob-container=<registry>/cronjob-henkel:${{ github.sha }} \
              -n datahub
```

The pipeline would use GitHub Secrets to store the DockerHub credentials and the cluster kubeconfig. Each image would be tagged with the Git commit SHA (not `latest`) so every deployment is traceable back to an exact commit. A PR to `main` would only run tests — the build and deploy steps would only trigger on a merge.

---

**Extend to other entity types.**
Add support for `CHART`, `DASHBOARD`, and `DATA_JOB` so the governance automation covers the full data stack, not just datasets. DataHub's GraphQL `search` input only accepts one entity type at a time, so the script would loop over a configurable list of types:

```python
ENTITY_TYPES = os.environ.get("ENTITY_TYPES", "DATASET,CHART,DASHBOARD,DATA_JOB").split(",")
for entity_type in ENTITY_TYPES:
    datasets = get_all_entities(entity_type)
    ...
```

---

**Slack / Teams notification.**
After each run, send a webhook message listing how many entities were tagged and their names. This closes the governance loop — the script tags them, the data team gets notified, and owners can be assigned. The webhook URL would be stored as an additional key in the existing SealedSecret.

---

**Add a Prometheus metric.**
Expose a metric `ownerless_datasets_total` counting how many datasets were tagged each run. With this, Grafana can show a trend over time and Alertmanager can fire an alert when the count exceeds a threshold — which would indicate a data ownership problem is growing faster than it is being resolved.

---

**Add a `--remove` mode.**
Once an owner is assigned to a dataset, the `needs-owner` tag should be removed automatically. A second mode in the script would search for datasets that have the tag but now have an owner, and remove the tag from them. This keeps the tag accurate as a real-time signal rather than a one-way label.

---

**Helm chart for the CronJob.**
Package the ConfigMap, SealedSecret, and CronJob into a small Helm chart. This would let the image tag, schedule, tag name, GMS URL, and entity types all be set with a single `helm upgrade` command rather than editing multiple YAML files individually. The CI/CD pipeline would then call `helm upgrade --install` instead of `kubectl apply`.

---

**Integration test with testcontainers.**
Write a test using `testcontainers-python` that spins up a real DataHub GMS container, ingests test datasets via the API, runs the script, and asserts the correct tags were applied. Right now the only way to test is against a live cluster. An automated integration test would also run inside the CI/CD pipeline on every PR before anything is merged.

---

**`--dry-run` as a per-job runtime flag.**
Instead of editing the ConfigMap (which affects all future scheduled runs), allow a one-off dry-run by passing an env override when creating the job manually:

```bash
kubectl create job dry-test --from=cronjob/tag-ownerless-cronjob -n datahub \
  --overrides='{"spec":{"template":{"spec":{"containers":[{"name":"cronjob-container","env":[{"name":"DRY_RUN","value":"true"}]}]}}}}'
```

---

## Teardown

```bash
helm uninstall datahub -n datahub
helm uninstall prerequisites -n datahub
kind delete cluster --name <your-cluster-name>
```