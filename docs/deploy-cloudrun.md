# Deploying the grading server on Google Cloud Run

This puts the grading server online **with the sale off**. Nothing can be
sold until the separate "turn the sale on" step, which adds the payout
address, the payment services and the sanctions screen.

The owner runs every command in Google Cloud Shell, in the project created
for RigGrade. No keys or passwords are shared with anyone.

## What gets created

| Item | Name | Expected cost |
|------|------|---------------|
| Container registry | `riggrade` in Artifact Registry | free tier (0.5 GB) |
| Storage bucket | `<project>-riggrade-state` | free tier (5 GB in us-central1) |
| Service account | `riggrade-server`, with access to that bucket only | free |
| Cloud Run service | `riggrade`, one instance at most, scales to zero | free tier |
| Image builds | Cloud Build | free tier |

The $5 budget alert is the backstop.

## Why a bucket

Cloud Run instances are temporary. Open sessions, the class references and
the sale log are written to a Cloud Storage bucket mounted at `/state`, so a
restart loses nothing. The server is limited to **one instance**, because
those files expect a single writer.

## Commands

Open https://console.cloud.google.com, choose the RigGrade project, and
click the Cloud Shell icon (`>_`) at the top right. Then run these blocks in
order.

**1. Settings.** Replace `YOUR_PROJECT_ID` with the project ID shown on the
console's project picker. The ID can differ from the project name.

```bash
PROJECT=YOUR_PROJECT_ID
REGION=us-central1
BUCKET=$PROJECT-riggrade-state
SA=riggrade-server@$PROJECT.iam.gserviceaccount.com
gcloud config set project $PROJECT
```

**2. Turn on the services** (about a minute):

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com storage.googleapis.com
```

**3. Get the code.** The repository is private, so sign in to GitHub
in the browser when asked. Choose "Login with a web browser", not a token.

```bash
gh auth login --hostname github.com --git-protocol https --web
gh repo clone nnyycc77-ship-it/riggrade && cd riggrade
```

**4. Create the registry, bucket and service account:**

```bash
gcloud artifacts repositories create riggrade --repository-format=docker --location=$REGION
gcloud storage buckets create gs://$BUCKET --location=$REGION \
  --uniform-bucket-level-access --public-access-prevention
gcloud iam service-accounts create riggrade-server --display-name="RigGrade server"
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member=serviceAccount:$SA --role=roles/storage.objectUser
```

**5. Build the server image** (a few minutes):

```bash
gcloud builds submit --config deploy/cloudbuild.yaml --substitutions=_REGION=$REGION .
```

**6. Deploy:**

```bash
gcloud run deploy riggrade \
  --image $REGION-docker.pkg.dev/$PROJECT/riggrade/server:latest \
  --region $REGION --service-account $SA \
  --execution-environment gen2 --min-instances 0 --max-instances 1 \
  --memory 512Mi --cpu 1 --allow-unauthenticated \
  --add-volume name=state,type=cloud-storage,bucket=$BUCKET,mount-options="uid=10001;gid=10001" \
  --add-volume-mount volume=state,mount-path=/state
```

`--allow-unauthenticated` makes the address public, so agents can reach
it. With the sale off, the server only describes itself and runs free
previews.

**7. Check it:**

```bash
URL=$(gcloud run services describe riggrade --region $REGION --format='value(status.url)')
curl -s $URL/riggrade/v1 | python3 -m json.tool | head -40
```

The answer should list the reasons the sale is off. Send Adviser 1 the
`URL` line. The address is public, so sharing it is safe.

## If something fails

- **Permission denied on `/state`.** Check that `mount-options` in step 6
  was copied exactly. The server runs as user 10001, not as root.
- **Billing not enabled.** Link the project to the billing account, then
  re-run step 2.
- **A "constraints/iam.allowedPolicyMemberDomains" error on step 6.** The
  account's organisation blocks public services. Tell Adviser 1; there are
  other ways.

## Updating later

From the `riggrade` folder in Cloud Shell, run `git pull`, then steps 5
and 6 again. To take the server offline, run:

```bash
gcloud run services delete riggrade --region $REGION
```
