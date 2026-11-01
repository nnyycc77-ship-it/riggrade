# Deploying the grading server on Google Cloud Run

This puts the grading server online **with the sale off**. Nothing can be
sold until the separate "turn the sale on" step, which adds the payout
address, the payment services and the sanctions screen.

Deploys run from GitHub Actions (`.github/workflows/deploy.yml`). The
workflow signs in to Google Cloud **without a key**: Google accepts
GitHub's short-lived sign-in token for this repository's `main` branch
only, through Workload Identity Federation. No password, key file or
secret is stored in GitHub or anywhere else.

The owner does a one-time setup in Cloud Shell (part A). After that, a
deploy is one click, or Adviser 1 starts it when the owner says "deploy".
It never runs by itself on a push.

## What gets created

| Item | Name | Expected cost |
|------|------|---------------|
| Container registry | `riggrade` in Artifact Registry | free tier (0.5 GB) |
| Storage bucket | `<project>-riggrade-state` | free tier (5 GB in us-central1) |
| Server account | `riggrade-server`: may use that bucket only | free |
| Deploy account | `riggrade-deployer`: may push images and update the Cloud Run service only | free |
| GitHub sign-in | pool `github`, provider `riggrade`: accepts `nnyycc77-ship-it/riggrade` on `main` only | free |
| Cloud Run service | `riggrade`, one instance at most, scales to zero | free tier |

The $5 budget alert is the backstop.

**Why a bucket.** Cloud Run instances are temporary. Open sessions, the
class references and the sale log are written to a bucket mounted at
`/state`, so a restart loses nothing. The server is limited to **one
instance**, because those files expect a single writer.

## Part A. One-time setup (owner, about 5 minutes)

Open https://console.cloud.google.com, choose the RigGrade project, and
click the Cloud Shell icon (`>_`) at the top right. Paste the blocks in
order. No code needs downloading.

**A1. Settings.** Replace `YOUR_PROJECT_ID` with the project ID from the
console's project picker. The ID can differ from the project name.

```bash
PROJECT=YOUR_PROJECT_ID
REGION=us-central1
REPO=nnyycc77-ship-it/riggrade
gcloud config set project $PROJECT
PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
SERVER_SA=riggrade-server@$PROJECT.iam.gserviceaccount.com
DEPLOY_SA=riggrade-deployer@$PROJECT.iam.gserviceaccount.com
```

**A2. Turn on the services** (about a minute):

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  storage.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com
```

**A3. Registry, bucket and the server account:**

```bash
gcloud artifacts repositories create riggrade --repository-format=docker --location=$REGION
gcloud storage buckets create gs://$PROJECT-riggrade-state --location=$REGION \
  --uniform-bucket-level-access --public-access-prevention
gcloud iam service-accounts create riggrade-server --display-name="RigGrade server"
gcloud storage buckets add-iam-policy-binding gs://$PROJECT-riggrade-state \
  --member=serviceAccount:$SERVER_SA --role=roles/storage.objectUser
```

**A4. The deploy account and its permissions:**

```bash
gcloud iam service-accounts create riggrade-deployer --display-name="RigGrade deployer (GitHub Actions)"
gcloud projects add-iam-policy-binding $PROJECT \
  --member=serviceAccount:$DEPLOY_SA --role=roles/run.admin --condition=None
gcloud artifacts repositories add-iam-policy-binding riggrade --location=$REGION \
  --member=serviceAccount:$DEPLOY_SA --role=roles/artifactregistry.writer
gcloud iam service-accounts add-iam-policy-binding $SERVER_SA \
  --member=serviceAccount:$DEPLOY_SA --role=roles/iam.serviceAccountUser
```

**A5. Let GitHub sign in, for this repository's main branch only:**

```bash
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub"
gcloud iam workload-identity-pools providers create-oidc riggrade \
  --location=global --workload-identity-pool=github \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='$REPO' && assertion.ref=='refs/heads/main'"
gcloud iam service-accounts add-iam-policy-binding $DEPLOY_SA \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"
```

**A6. Print the two values to send to Adviser 1.** Neither is a secret:
they only name the project.

```bash
echo "GCP_PROJECT=$PROJECT"; echo "GCP_PROJECT_NUMBER=$PROJECT_NUMBER"
```

## Part B. Adviser 1 (no owner action)

1. Fill in `deploy/gcp.env` with the two values and merge.
2. When the owner says "deploy", start the `deploy` workflow on `main`.
   The workflow does the following:
   - runs the tests;
   - builds the server image;
   - pushes the image to the registry;
   - deploys to Cloud Run;
   - prints the service address and the reasons the sale is off.

The owner can also start a deploy at any time: open the repository on
GitHub, then **Actions → deploy → Run workflow** on `main`.

## If something fails

- **Permission denied on `/state`.** The server runs as user 10001; the
  deploy step mounts the bucket for that user. Check that block A3 ran in
  full.
- **"Billing not enabled".** Link the project to the billing account, then
  re-run block A2.
- **An error naming `iam.allowedPolicyMemberDomains` at the deploy step.**
  The account's organisation blocks public services. Tell Adviser 1; there
  are other ways.
- **Sign-in fails in the workflow** (`unauthorized_client` or
  `Permission 'iam.serviceAccounts.getAccessToken' denied`). Re-run block
  A5. Check that `GCP_PROJECT_NUMBER` is the number, not the ID.

## Taking it offline, or undoing the GitHub sign-in

To remove the service:

```bash
gcloud run services delete riggrade --region us-central1
```

To stop GitHub deploying (the service keeps running):

```bash
gcloud iam workload-identity-pools providers disable riggrade --location=global --workload-identity-pool=github
```

## Without GitHub Actions (manual path)

These commands run from a clone of the repository in Cloud Shell, after
part A. Sign in to GitHub in the browser when asked; choose "Login with a
web browser", not a token.

```bash
gh auth login --hostname github.com --git-protocol https --web
gh repo clone nnyycc77-ship-it/riggrade && cd riggrade
gcloud services enable cloudbuild.googleapis.com
gcloud builds submit --config deploy/cloudbuild.yaml --substitutions=_REGION=$REGION .
gcloud run deploy riggrade \
  --image $REGION-docker.pkg.dev/$PROJECT/riggrade/server:latest \
  --region $REGION --service-account $SERVER_SA \
  --execution-environment gen2 --min-instances 0 --max-instances 1 \
  --memory 512Mi --cpu 1 --allow-unauthenticated \
  --add-volume "name=state,type=cloud-storage,bucket=$PROJECT-riggrade-state,mount-options=uid=10001;gid=10001" \
  --add-volume-mount volume=state,mount-path=/state
```
