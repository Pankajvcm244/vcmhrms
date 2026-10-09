# Temp Worker — initial implementation

Four user-facing DocTypes: Temp Worker, Temp Shift, Temp Roster,
Temp Attendance. Two child DocTypes: Temp Roster Duty and Temp Attendance Duty.
Temp Punch is a technical immutable event store. A separate event table is
deliberate: unique event IDs, concurrent retries, unrostered punches, and cross-midnight
matching are safer than editing a growing child table on a calendar-day attendance record.

## Installation in the existing vcmhrms app

1. Copy `vcmhrms/temp_worker/` into your app package.
2. Append `Temp Worker` to `vcmhrms/modules.txt`.
3. Merge the hook additions in `HOOKS_ADDITIONS.py` into your existing hooks.py.
   Preserve any existing after_migrate callbacks; append this callback rather than replacing them.
4. Back up the staging site; run `bench --site YOUR_SITE migrate`, `bench build --app vcmhrms`,
   then restart the bench processes using your usual deployment procedure.
5. Assign Temp Worker Manager or Temp Worker Supervisor to staff.
   Each non-System Manager needs Branch User Permission(s). This is deliberately fail-closed.
6. Assign only Temp Worker Integration to a dedicated API user; create API credentials.
7. Create shifts and workers, then open `/app/temp-monthly-roster`.
   Forms are available through Desk search. Workspace/dashboard layout remains pending team input.

## SmartOffice API

POST `/api/method/vcmhrms.temp_worker.api.receive_punches`

Authenticate with `Authorization: token API_KEY:API_SECRET` and Content-Type application/json.
Example:

```json
{
  "punches": [
    {"event_id":"SO-1001","person_id":"900001","punch_time":"2026-10-08T22:00:00+05:30","device_id":"VRN-01","location":"Vrindavan"},
    {"event_id":"SO-1002","person_id":"900001","punch_time":"2026-10-09T06:00:00+05:30","device_id":"VRN-01","location":"Vrindavan"}
  ],
  "synced_through":"2026-10-09T07:00:00+05:30"
}
```

Use globally unique event IDs and globally unique person IDs across all SmartOffice locations.
Worker SmartOffice IDs are numeric strings beginning with 9. No IN/OUT is required or stored.
Timestamp offsets are mandatory; stored timestamps use the site's timezone.
API returns per-event created/duplicate/failed outcomes. Retry failed rows; unchanged event IDs
are idempotent. Conflicting event IDs are rejected. Unknown workers must be registered then retried.
The watermark is GLOBAL: send it only when ALL locations have delivered ALL events through that
time. Do not advance it for one location while another is offline. Failed batches do not advance it.
Without a confirmed watermark, results remain Pending; never infer absence from an offline feed.
The queue recalculates all rosters after a batch in this first small-scale release.

## Calculation and scope

- Roster duty start/end datetimes support overnight and multiple shifts. Date means duty-start date.
- First/last punch within a window infer IN/OUT. Break minutes are subtracted once per duty.
- Flexy uses explicit per-day start/end attendance windows and required hours. This release does
  not apply late-entry deductions or automatically move windows relative to arrival.
- A punch eligible for multiple duty windows makes those duties Needs Review with zero counted
  hours; the system does not invent a boundary punch or split continuous hours.
- Single punch is Needs Review. Absent is calculated only after shift windows and sync completeness.
- No government holiday calendar is consulted. Weekly off is explicit in roster.
- Monthly page shows Monday–Sunday weekly-off warnings for complete weeks inside the selected
  month. They are advisory, not a publishing gate; cross-month and partial-engagement weeks need
  supervisor review. Confirm the business week definition before enforcing publication.
- Bulk allocation replaces a worker's existing duty rows in the selected range with one shift or off.
  Add multiple duties through the daily roster form.
- OT is only calculated extra hours; approval requires manager role and review notes. There is no
  payroll posting or automatic approval. Approved OT is a daily total, not one value per duty.
- Roster edits recalculate affected adjacent days. Historical reports reference current roster;
  Frappe track_changes preserves change history. No approval/finalization workflow yet.
- Weekly-off punches are flagged for review; replacement off/OT treatment is not automatic.
- Raw punches cannot be edited/deleted through ordinary document operations.
- Separate punch log list captures unrostered events; they are processed when a roster is added.
- Temple devotees and final dashboard are outside this initial implementation.

## Validation before production

Local pure engine tests cover overnight, missing punches, pending sync, boundary ambiguity,
separated multiple shifts, duplicate timestamps, breaks and OT. Python/JSON/JS syntax is checked.
No live Frappe bench was available during development. On staging, verify migration, roles,
Branch restrictions, form save, API authentication, retries, concurrent events, monthly page,
background worker processing and report with your installed v15 patch version.
