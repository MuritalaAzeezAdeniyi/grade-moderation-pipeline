# Grade Moderation Pipeline spec

Purpose: markers differ in strictness. The tool ingests batch scores, applies
documented moderation rules, raises variance alerts, lets a reviewer approve
adjustments, and exports only approved results. It must be restartable,
auditable and reproducible.

## Pipeline stages (in order)
INGEST -> MODERATE -> ALERTS -> REVIEW -> EXPORT

## Input CSV
Header required: student_id, marker_id, subject, raw_score.
Row validation (trim spaces first):
- MISSING_FIELD: any of the four fields empty
- INVALID_SCORE: raw_score not a number, or more than 2 decimals
- OUT_OF_RANGE: raw_score < 0 or > max_score
- DUPLICATE_ROW: same (student_id, subject) already seen in this file; the
  first row is kept, later rows rejected
Rejected rows are stored (line number, reason code, original text), never
silently dropped. A file with a wrong header or no valid rows is refused
whole (400) and creates no batch.
Idempotent ingest: sha256 of the file bytes. The same hash returns the
existing batch (200) and creates nothing.

## Rules file (rules/rules_v1.json)
{"version":"v1","max_score":100,"min_scripts_per_marker":20,
 "deviation_threshold":5,"max_adjustment":8,"min_stdev":3,
 "grade_bands":[{"grade":"A","min":70},{"grade":"B","min":60},
 {"grade":"C","min":50},{"grade":"D","min":40},{"grade":"F","min":0}]}
When a batch is moderated, store a snapshot of the rules JSON and its sha256
on the batch. Changing the file never changes past batches.

## Moderation (deterministic; use Decimal, never float)
Per subject separately:
- cohort_mean = mean of all valid raw scores in the batch for that subject
- for each marker with n >= min_scripts_per_marker: marker_mean, population
  stdev, deviation = marker_mean - cohort_mean
- all means, stdevs and deviations are quantized to 4 decimal places (round
  half up) before any comparison
- if abs(deviation) > deviation_threshold:
  delta = -clamp(deviation, -max_adjustment, +max_adjustment), rounded half
  up to a whole number
  for each of that marker's scores:
  adjusted = round_half_up(clamp(raw + delta, 0, max_score)) to a whole number
- markers within the threshold, or with n < min_scripts_per_marker, get no
  adjustment (final score = raw score)
- each adjustment row stores: student_id, marker_id, subject, raw_score,
  delta, adjusted_score, grade_before, grade_after, rule_id
  "MARKER_BIAS_V1", the facts (marker_mean, cohort_mean, deviation,
  threshold, n) as JSON, and status PROPOSED
- marker stats (n, mean, stdev, cohort_mean, deviation) are stored per
  batch, marker and subject

## Alerts (for humans; they never change scores)
- MARKER_DEVIATION (warn): the marker was adjusted
- ADJUSTMENT_CAPPED (warn): abs(deviation) > max_adjustment
- LOW_SPREAD (warn): n >= min_scripts_per_marker and stdev < min_stdev
- INSUFFICIENT_SAMPLE (info): n < min_scripts_per_marker, not moderated
- GRADE_CHANGED (info): count and percentage of a marker's scripts whose
  grade band changed
Each alert: batch_id, marker_id, subject, alert_type, severity, details JSON.
Deterministic order.

## Review and export
- A reviewer decision is per (batch, marker, subject): APPROVED or REJECTED,
  with reviewer name and optional note. Only markers with proposed
  adjustments need a decision. A decision cannot be changed (409).
- Export is allowed only when every (marker, subject) with proposed
  adjustments has a decision; otherwise 409.
- Export CSV columns: student_id, marker_id, subject, raw_score, final_score,
  adjusted, rule_version, batch_id; sorted by subject, student_id.
  final_score = adjusted_score if APPROVED, otherwise raw_score.
- Exporting again returns the same file (same sha256) and creates no second
  export record.

## Restartable
stage_runs table: batch_id, stage, status (pending, in_progress, completed,
failed), started_at, finished_at, error.
A stage claims atomically (pending -> in_progress) and writes all its output
and its "completed" status in ONE database transaction. If it fails or the
process dies, no partial output exists. Resume resets in_progress or failed
stages to pending and reruns them; completed stages are skipped, so a batch
is never moderated twice (no double adjustments). Local stages are atomic, so
retrying is safe. Timestamps are metadata only and never used in calculations.

## Reproducibility
output_hash = sha256 of a canonical text form of all adjustments sorted by
(subject, student_id), stored on the batch after MODERATE. Same file + same
rules => same output_hash.

## Audit log
audit_events: id, timestamp (UTC), batch_id, actor, event_type, details JSON.
Append-only: code never updates or deletes, and SQLite triggers abort UPDATE
and DELETE. Events: BATCH_INGESTED, DUPLICATE_UPLOAD, STAGE_STARTED,
STAGE_COMPLETED, STAGE_FAILED, REVIEW_DECISION, EXPORT_CREATED,
EXPLANATION_PROPOSED. Actor is "system" or the reviewer name.

## API (JSON; every error is {"error": "<message>"})
POST /batches (multipart file) -> 201 new batch, 200 existing batch
GET /batches; GET /batches/{id}
POST /batches/{id}/run (runs pending stages)
POST /batches/{id}/resume
GET /batches/{id}/adjustments (optional marker_id, subject)
GET /batches/{id}/alerts; GET /batches/{id}/rejected-rows
POST /batches/{id}/review {marker_id, subject, decision, reviewer, note}
POST /batches/{id}/export -> {export_id, sha256, row_count}
GET /batches/{id}/export/download -> the CSV
GET /batches/{id}/audit
Status codes: 400 invalid input, 404 not found, 409 invalid state.

## Optional AI explanations
Stored in a separate explanations table with status PROPOSED. They never
modify adjustments or exports. Provider "template" is the default; provider
"api" is behind an environment variable and is mocked in tests. Check: every
number in an explanation must appear in the facts given to the generator,
otherwise status REJECTED_BY_CHECK.

## Out of scope
Authentication, multiple users, deployment.
