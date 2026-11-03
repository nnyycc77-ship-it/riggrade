# Marketplace templates for the free preflight

Status: drafted 3 November 2026 for publishing by the coordinator, on the owner's direction (option A). Not
published yet. Field names follow each marketplace's template form as Adviser 1 understands it; the coordinator
checks them in the form at publish time, and any field that differs is corrected here.

A template puts the free preflight where renters already are: they pick the template when they rent, and the
check runs once on start. It changes nothing on the box. No results are sent anywhere. It writes its verdict to
`/workspace/riggrade-preflight.txt` and `/workspace/preflight-report.json` (`docs/preflight.md`).

## Rules for both templates

- **No referral money.** If a marketplace pays template creators in credits or cash, RigGrade declines it, or
  leaves it unused and says so here, because those marketplaces' hosts are graded parties
  (`docs/independence.md` section 3).
- **No claim of endorsement.** The README says RigGrade is not affiliated with the marketplace.
- **Pinned version.** The start line installs exactly `riggrade==0.2.0`, so a later release cannot change the
  preflight a renter gets without a change here (coordinator condition (b), Message 310).
- **The base image is the marketplace's own current PyTorch image**, so the template adds nothing to the box but
  the pip package. The tag is chosen at publish time and written below.

## Vast.ai

| Field | Value |
|---|---|
| Template name | RigGrade preflight (free GPU check on start) |
| Image | the current official Vast PyTorch image (`vastai/pytorch`); tag written here at publish |
| Launch mode | the image's default (Jupyter or SSH), unchanged |
| On-start script | see below |
| Disk | the image's default; the check needs under 1 GB free |
| Visibility | public |
| README | see below |

On-start script:

    pip install --quiet "riggrade==0.2.0" && \
      riggrade-preflight --dir /workspace > /workspace/riggrade-preflight.txt 2>&1 &

## RunPod

| Field | Value |
|---|---|
| Template name | RigGrade preflight (free GPU check on start) |
| Type | Pod |
| Container image | the current official RunPod PyTorch image (`runpod/pytorch`); tag written here at publish |
| Container start command | see below; it ends by handing over to the image's own `/start.sh` |
| Container disk | the image's default |
| Volume mount path | `/workspace` |
| Ports | the image's defaults |
| Visibility | public (community template) |
| README | see below |

Container start command:

    bash -c "pip install --quiet 'riggrade==0.2.0' && \
      (riggrade-preflight --dir /workspace > /workspace/riggrade-preflight.txt 2>&1 &); /start.sh"

## README text (both templates)

> **Free one-minute GPU check on start.** When this pod starts, it runs the open-source RigGrade preflight
> (`pip install riggrade`, Apache-2.0) once in the background. It checks whether the box behaves like the card it
> reports: memory or compute already in use by someone else, clocks and throttling under load, temperature, PCIe
> link, driver against CUDA, ECC errors, memory bandwidth, host CPU, memory and swap, disk write and download
> speed. The verdict (pass, warn or fail, with
> numbers) is in `/workspace/riggrade-preflight.txt` and `/workspace/preflight-report.json` about a minute after
> start.
>
> No results are sent anywhere unless you run it yourself with `--share`, which sends an anonymised summary. It does
> not change the box. A warn or fail is a measurement, not a judgement of the host, and you decide what to do with
> it under the marketplace's own terms.
>
> RigGrade is a single-operator service with open rules, not affiliated with this marketplace, and takes no
> referral money from it. Method and code: https://github.com/nnyycc77-ship-it/riggrade

## Checks before publishing (coordinator)

1. The form's field names match the tables above (they are Adviser 1's understanding, not verified), and the base
   image tag is filled in. Any field that differs is corrected here before publishing.
2. One start on the cheapest box of each marketplace shows the verdict files appearing within about two minutes,
   and the box's own SSH or Jupyter still works. That start costs a few cents of rental, which is the owner's
   spend to approve.
3. Whether either marketplace pays template creators, and the answer is written under "Rules" above.
