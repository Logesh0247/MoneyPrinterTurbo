# Automation system audit and verification record

This is a prioritized audit of inspected paths, not a claim that every module,
button or external service was tested. See `production-audit.md` and
`account-security-status.md` for prior findings and existing local test evidence.

## Initial inspection (before this iteration's changes)

| Area | Evidence and status |
| --- | --- |
| Frontend | Streamlit (`webui/Main.py`), local background-thread job manager and progress fragments. Existing status and stage view do not prove provider completion. Browser/device storage can contain credentials and bearer sessions. |
| Backend | FastAPI and CLI call the same Python services; API authentication can be disabled by empty configuration. Task storage is memory or Redis, not inherently durable across all deployments. |
| Accounts and data | JSON account file with file locking and hashed passwords; app settings, tasks and media are shared globally. This is **not** multi-user isolation or a relational database. |
| AI/agents | LLM/script and search-term services plus provider-specific material/TTS adapters; there is no verified end-to-end autonomous agent framework with research, fact/rights checking and analytics handoff. Provider responses and model access require live verification. |
| Generation | `task.py` runs script → search terms → narration → subtitles → materials → rendering. There are separate cinematic/provider paths. Stages, quality and sync are not equivalent to a comprehensive production QC pass. |
| Media | FFmpeg/MoviePy render and uploaded/downloaded-video validation exist; sampled finished-video inspection is advisory. Actual provider output, continuity, audio quality and full output validity need independent checks. |
| Publishing | Official YouTube OAuth, uploader and mandatory QC are missing. `youtube_safety.py` fails closed; legacy upload and daily queue must not bypass it. No upload or scheduling is validated. |
| Scheduler | `daily_queue.py` uses a separate single worker and file-based queue; it stops under Safe Mode or uncertain outcomes. Process-local WebUI workers cannot survive restarts. Hosted background-worker availability is not verified. |
| Cost | Selected paid video integrations have quoting/confirmation; complete per-video spend, actual provider quotas and account-wide enforced budgets are not verified. |
| Observability and security | Existing stage history and bounded logs are useful, but browser UI, JSON account storage, global configuration, and provider exception paths require further secret review. No evidence of comprehensive centralized monitoring, durable retries, safe checkpoints or user isolation. |
| Deployment | Render free tier can sleep and its filesystem is ephemeral. Protection, branch version, credentials, provider access and remote worker operation are not confirmed. |

## Immediate priorities

1. Preserve YouTube Safe Mode and do not perform uploads or generate paid work without configured consent.
2. Fix verified local security or reliability bugs without weakening task error visibility or changing rendering settings.
3. Add integration/regression tests; separately identify skipped and externally blocked tests.
4. Treat secure storage, user isolation, verified publishing, mandatory QC, durable recovery and cost limits as release blockers.

## End-to-end test plan and status

| Requested test | Current status / reason |
| --- | --- |
| Idea, script, storyboard, visuals and video scenes | Not live verified: configured provider/model access, credits and external network required; storyboard and agent handoff are not established end to end. |
| Voice, scenes/audio, subtitles and editing | Local component tests exist, but live TTS and a complete end-to-end generated output have not been verified in this audit. |
| Thumbnail and SEO metadata | Not verified as a complete provider-to-publish workflow. |
| Mandatory quality validation | Not implemented in full; existing video inspection is advisory, not approval. |
| Private upload, metadata/thumbnail, scheduling and analytics | Blocked: official YouTube OAuth/upload/analytics and mandatory checks are absent. No real upload attempted. |
| Failure and restart recovery | Local unit/integration simulations exist; durable complete pipeline recovery and deployed cloud-worker restart recovery are not established. |

## Iteration changes

- The final render now undergoes a complete FFmpeg decode of its primary video
  and audio streams before replacing the final output path. A timeout, missing
  stream, invalid file or decoder failure prevents promotion; an older output
  remains intact. This adds a bounded decode pass and therefore can increase
  processing time and CPU use. It does not detect all perceptual defects,
  establish lip-sync, detect silent/black content, or satisfy publishing QC.
- The batch pipeline reports an integrity-check failure without scheduling a
  publish and retains already completed batch results.
- WebUI worker/scheduler boundary errors no longer persist or log raw exception
  messages. Existing task progress is retained on worker failure, and a deleted
  task is not recreated by the worker's failure handler. Queue-full errors
  remain distinguishable and the UI gives an actionable message.
- The broader app still has other exception and logging paths; this does not
  certify that secrets are absent from every log or browser response.

## Actual verification results

- **WORKING AND VERIFIED (local):** FFmpeg-generated synthetic video and audio
  were combined, rendered and completely decoded through the task's actual
  final-video path. Tests also reject missing-audio, corrupt, truncated and
  timed-out output without overwriting a previous file. These are local media
  integration tests, not live AI generation or a YouTube upload.
- **WORKING AND VERIFIED (local):** Worker exception redaction, preserved
  progress, deletion behavior, safe queue-full reporting and failed-batch
  no-publish behavior were exercised in regression tests.
- **Local full suite:** 3,242 passed, 162 skipped, 12,939 subtests passed,
  3 warnings. Ruff and `git diff --check` passed. Skipped tests and tests with
  mocked providers do not verify external integrations.
- **IMPLEMENTED BUT NOT VERIFIED live:** Existing provider adapters, Edge TTS,
  OAuth sign-in, task generation, scheduled queue and optional cross-posting.
  No external API connection, paid model, billing/quota, hosted worker, mobile
  browser, or live deployment was checked in this iteration.
- **REQUIRES API CREDENTIALS / EXTERNAL CONFIGURATION:** Provider credentials,
  access, quota and model entitlement; OAuth public URL/client configuration;
  private persistent storage, protected hosting and a separately managed worker.
  Keep secrets in host-managed configuration, not source control or chat.
- **NOT IMPLEMENTED / BLOCKED:** Complete autonomous research-to-analytics
  workflow; durable, safe stage resume for all providers; complete spending
  enforcement; strict user isolation; mandatory rights/factual/quality/approval
  checks; official YouTube OAuth publishing, scheduling and analytics. Safe
  Mode intentionally continues to block YouTube publication.
- **FAILED:** No new local regression failures observed in the completed run.
  Absence of an observed failure does not imply untested external features work.

## Requested end-to-end tests

Research/idea, LLM script, storyboard, provider visuals/scenes, real voiceover,
subtitles, thumbnail, SEO, quality gate, private YouTube upload, metadata,
scheduling and analytics were **not run end to end**. Real API permissions,
credentials, a secure environment and compliant publishing implementation are
required. Error and restart behavior has local test coverage, but durable
cloud-process recovery was not tested. Previously exposed keys should be
rotated before further use.
