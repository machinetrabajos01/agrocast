# AGROCAST — Incident reporting and user feedback procedures

## Channels

1. **In the system** (preferred): Planning → Support & Feedback. Available to every role.
2. **GitHub issues** in the repository, for technical defects found by the development team.

## Report types and severity

| Type | Examples |
|---|---|
| `bug` | page error, wrong calculation, button that does not work |
| `data_issue` | missing or wrong index month, outdated SIPSA-I price |
| `model_issue` | recommendation that contradicts market evidence, unexpected interval |
| `access` | cannot sign in, missing permission |
| `feedback` | suggestions and improvement requests |

| Severity | Definition | Response target |
|---|---|---|
| high | system unavailable, wrong data used for decisions, security problem | same business day |
| medium | a function fails but there is a workaround | 2 business days |
| low | cosmetic issue, suggestion | weekly review |

## Workflow

1. **Open** — the user reports the problem with a description and, if possible, the plan or inference number.
2. **In progress** — an analyst or administrator assigns the report to a responsible person.
3. **Resolved** — the fix or answer is written in the *Resolution* field (mandatory).
4. **Closed** — the report is archived after the user is informed.

Server errors are captured automatically in the operational log (`http.error`) even if the user does
not report them; the administrator reviews errors daily and opens an incident when needed.

## Weekly review

- Open reports by severity and age.
- `model_issue` reports are compared with the monitoring page; repeated reports for the same series
  trigger a drift and performance check.
- `feedback` reports are prioritized into the next version backlog.
