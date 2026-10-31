# RigGrade sample grading

> SAMPLE: fictional rig, fictional numbers, not a live grade. The three rigs below do not exist. Their numbers are invented, in sample units against a sample reference of 1000, and chosen only to show how the grading rules behave. The grades were produced by the real grading engine (`python3 -m samples.make_samples`).

## Fictional rig A: H100 SXM class, inference, full grade

**Badge:** Grade 1 · H100 class · Inference · full grade · declared host run, attested · sample

Attested evidence, steady passes and a clean 30-day heartbeat: every pillar earns Grade 1, and attestation lets the overall grade stay at Grade 1.

| | |
|---|---|
| Grade | 1 |
| Form | full (trailing windows) |
| Capped by | nothing (all pillars at Grade 1) |
| Pillars | consistency 1, reliability 1, speed 1 |
| Speed ratio | 0.978 |
| Status | assigned |
| Evidence tag | `declared:host attestation:attested` |

## Fictional rig B: RTX 4090 class, inference, point grade

**Badge:** Grade 2 · RTX 4090 class · Inference · point grade, single session · capped by Consistency · declared buyer run, unattested · sample

Speed earns Grade 1, but the latency spread (p99/p50 of 2.6) earns Grade 2 on consistency, and the weakest pillar caps the grade. It is a single paid session, so reliability is not measured.

| | |
|---|---|
| Grade | 2 |
| Form | point (single session) |
| Capped by | consistency |
| Pillars | consistency 2, reliability not measured (point grade), speed 1 |
| Speed ratio | 0.962 |
| Status | assigned |
| Evidence tag | `declared:buyer attestation:unattested` |

## Fictional rig C: A100 SXM class, training, full grade on watch

**Badge:** Grade 1 · A100 class · Training · full grade · on watch · declared host run, attested · sample

The rig held Grade 1. This week its speed ratio is about 0.91, below the 0.92 keep bar. Under watch-before-demotion it keeps Grade 1 and goes on watch, and the badge says so. A second consecutive week below the keep bar would land the grade on what the numbers earn, Grade 2.

| | |
|---|---|
| Grade | 1 |
| Form | full (trailing windows) |
| Capped by | nothing (all pillars at Grade 1) |
| Pillars | consistency 1, reliability 1, speed 1 |
| Speed ratio | 0.909 |
| Status | on_watch |
| Evidence tag | `declared:host attestation:attested` |

All three labels carry the statement: "Measured, not promised. A label of measured quality under the named test and window; it makes no promise about future delivery."
