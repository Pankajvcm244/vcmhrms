# Agency Worker — initial implementation

Four user-facing DocTypes: Agency Worker, Agency Shift, Agency Roster,
Agency Attendance. Two child DocTypes: Agency Roster Duty and Agency Attendance Duty.
Agency Punch is a technical immutable event store. A separate event table is
deliberate: unique event IDs, concurrent retries, unrostered punches, and cross-midnight
matching are safer than editing a growing child table on a calendar-day attendance record.

Share [Agency Attendance Guide](AGENCY_ATTENDANCE_GUIDE.md) with HR, supervisors, testers, and the
biometric integration team for status codes, synchronization, calculation, OT, and review behavior.

## Agency workflow

- Agency Worker is the staff master. `Agency Staff ID` and biometric code remain separate identifiers.
- Each staff member may have multiple Roster Managers and multiple Supervisors. Roster Managers maintain shifts; Supervisors resolve punch exceptions and OT. System Manager remains the emergency backup.
- The Agency master links to a separate Agency Holiday List and holiday colour. It does not use the HRMS Holiday List for regular employees. Department and Budget Location stay on the staff record.
- Agency Monthly Attendance covers the 26th of the previous month through the 25th of the selected payroll month. A daily `✓` is computed automatically only when that staff/date has no pending attendance, punch-review, or OT action. HR Head only monitors this report.
- `Lock Completed Period` refuses to lock while any supervisor action is pending. Once locked, server-side validation blocks roster, attendance, punch-review, and OT changes. Later biometric events may still be stored for audit, but they do not recalculate a locked period.

## Installation in the existing vcmhrms app

This version uses a clean reset instead of migrating or renaming Temp records. Delete the old
records and standard documents manually in Desk using [`UI_DOCUMENT_CHECKLIST.md`](../../UI_DOCUMENT_CHECKLIST.md). Take a database
backup first.

1. Remove the old `vcmhrms/temp_worker/` source folder.
2. Copy `vcmhrms/agency_worker/` into the app package.
3. Replace `Temp Worker` with `Agency Worker` in `vcmhrms/modules.txt`.
4. Create the new DocTypes, Pages and Reports in the order in [`UI_DOCUMENT_CHECKLIST.md`](../../UI_DOCUMENT_CHECKLIST.md).
5. Copy the controller files plus the updated hooks and workspace files, then run `bench migrate`, `bench build` and
   `bench clear-cache`.
6. Restart the bench processes using your normal deployment procedure.
7. Assign Agency Worker Manager, Agency Roster Manager, or Agency Worker Supervisor as required.
   Each non-System Manager needs Budget Location User Permission(s). This is deliberately fail-closed.
8. Assign only Agency Worker Integration to a dedicated API user; create API credentials.
9. Create agencies, shifts and agency staff, assign their roster managers and supervisors, then
   open `/app/ag-monthly-roster`.

## SmartOffice API

## Manual tester UI

After migration, open `/app/ag-punch-simulator` (also accessible from a Agency Roster form).
Only System Manager and ERP Manager can use this page; Integration role and API credentials
are not required for a signed-in tester. Non-System Managers still need Budget Location User Permissions.

1. Create the workers, shifts and duty rosters before submitting punches.
2. Select ONE Agency Worker. Add multiple rows in Punch Times, including timestamps across dates.
   Example: 5 Oct 22:00, 6 Oct 06:00, 6 Oct 22:00, 7 Oct 06:00 for two night duties.
3. Generate Sample optionally appends Day, Night or Multiple Duties times for a chosen date.
4. Build Preview checks timestamps and generates event IDs. Submit Test Punches automatically
   builds this preview if needed, then submits all timestamps for the selected worker.
5. Optional Advanced JSON upload/editor must contain only that selected worker's Biometric Code.
   After editing JSON, click Preview JSON to use JSON mode. Download JSON supports sharing tests.
6. Submit shows per-row created/duplicate/failed results. Open Punch Log to inspect saved events.
   Attendance recalculates in the background; authorized roster editors can also recalculate manually.

ERP Manager receives read access to the module masters and punch log. Assign Budget Location User
Permissions for permitted locations; ordinary Agency Worker Manager/Supervisor cannot use the simulator.
Both Page roles and server methods enforce this restriction, including direct API calls.

Generated scenarios assume Day 09:00–18:00 with 60-minute unpaid break, Night 22:00–06:00 without
break, and separate Morning 06:00–10:00 / Evening 14:00–18:00 duties without breaks. The offset
defaults to +05:30; choose the correct source offset and matching roster dates.

Every simulated event is marked `is_simulated=1`. Events remain immutable and affect actual
attendance records, so use an isolated staging site. Keep event IDs unchanged to test duplicates;
generate a new sample for a fresh scenario. Simulated events and normal SmartOffice events share
the same event-ID uniqueness constraints and ingestion/calculation logic.

Attendance finalizes automatically after each duty's complete punch and OT capture window closes.
A scheduler at 04:00 finalizes eligible day/evening records, and another run at 16:00 handles night
shifts ending in the morning, including their configured after-shift capture window. Managers can run
the same process from **Agency Attendance Review > Process Attendance** for testing or an operational recheck. Delayed
historical punches remain accepted and recalculate only nearby rosters for that worker.

## API contract

POST `/api/method/vcmhrms.agency_worker.api.receive_punches`

Authenticate with `Authorization: token API_KEY:API_SECRET` and Content-Type application/json.
Example:

```json
{
  "punches": [
    {"biometric_code":"900001","punch_time":"2026-10-08 22:00:00"},
    {"biometric_code":"900001","punch_time":"2026-10-09 06:00:00"}
  ]
}
```

Punch times without a UTC offset are interpreted in the ERPNext site's configured
timezone (`Asia/Kolkata`). ISO timestamps containing an offset, such as
`2026-10-08T22:00:00+05:30`, remain supported for compatibility.

Biometric Code uniquely identifies the Agency Worker; the vendor does not send Staff ID. Biometric Codes
are numeric strings beginning with 9. No IN/OUT or external event ID is required. ERPNext generates a deterministic
event ID internally, so retrying an unchanged punch is idempotent.
API returns per-event created/duplicate/failed outcomes. Retry failed rows; unchanged event IDs
are idempotent. Conflicting event IDs are rejected. Unknown workers must be registered then retried.
The provider does not send a synchronization watermark. Each newly accepted punch queues recalculation
only for that worker's nearby roster dates, including adjacent dates needed for overnight duties.

## Calculation and scope

- Agency Worker has two separate identifiers: manually entered `Staff ID` for the organization and
  `Biometric Code` for biometric/API matching. They are never substituted for one another. New
  document IDs use `AW-YYMM-xxxxx`.
- New attendance IDs use `AGENCYATT-YYMM-xxxxxx`. New punch IDs use
  `AgencyPunch-YYMMDD-xxxxxx`; Staff ID is stored as a field and is not part of the document ID. Agency Attendance does not duplicate Staff ID; it retains Worker
  Code and the Agency Staff Name link.
- New roster IDs use `AGENCYROST-YYMM-xxxxx`, where YYMM comes from the roster date.
- Roster duty start/end datetimes support overnight and multiple shifts. Date means duty-start date.
- First/last punch within a window infer IN/OUT. Break minutes are subtracted once per duty.
- Flexy uses explicit per-day start/end attendance windows and required hours. This release does
  not apply late-entry deductions or automatically move windows relative to arrival.
- A punch eligible for multiple duty windows makes those duties Needs Review with zero counted
  hours; the system does not invent a boundary punch or split continuous hours.
- Single punch is Needs Review. Absent is calculated only after the complete shift capture window closes.
- Agency-specific special holidays come only from the Agency Holiday List selected on the Agency master.
  Weekly off remains explicit in the roster.
- Bulk allocation replaces a worker's existing duty rows in the selected range with one shift or off.
  Add multiple duties through the daily roster form.
- For the normal 150-worker workflow, load **Agency Monthly Roster**, select workers with the checkboxes,
  and choose **Bulk Assign Simple Shift**. Select a date range, one shift and an optional weekly-off
  weekday. **Skip Existing** is the default and preserves dates already customized with OT, another
  shift or multiple duties. Use the grid's date button for those exceptions. **Replace Existing**
  deliberately clears and rebuilds existing dates. A one-worker assignment completes immediately;
  larger selections use Frappe's standard background queue and send the initiating user a completion
  summary.
- Each roster duty can plan before-shift OT and after-shift OT separately, with a mandatory reason.
  Each shift also defines maximum before/after OT capture hours (default 4). This lets actual OT
  exceed planned OT while retaining the planned-versus-actual difference. Actual OT is the time from first punch to scheduled
  start and scheduled end to last punch. Values are calculated independently for every duty.
- Where broad capture windows from adjacent duties overlap, matching boundaries are split at the
  midpoint between duties. A punch exactly on a shared boundary remains Needs Review.
- Each shift has late-entry and early-exit grace minutes, default 15. First punch later than shift
  start plus grace is flagged Late Entry; last punch earlier than shift end minus grace is flagged
  Early Exit. Exactly 15 minutes is within grace.
- Final status uses worked hours (first to last punch minus configured break): required hours or more
  is Present; from the shift's configured Half Day Hours up to the requirement is Half Day; less than
  the configured Half Day Hours is Absent. When Half Day Hours is zero, half the required hours is used.
- `/app/ag-attend-review` is the manager dashboard for missing punches, Half Day, Absent, Late
  Entry and Early Exit. Managers see only their assigned workers; System Manager is backup. A manager
  can record a corrected IN/OUT pair or override status with a mandatory reason. Raw punches remain
  unchanged; correction login/time/reason are stored, and recalculation resets affected approvals.
  The manager may also approve the calculated punch exception as-is. Punch review stores approver,
  timestamp and remarks. Normal Present attendance is marked Not Required and never enters this queue.
- Agency Worker Supervisor or System Manager may approve before/after OT up to its actual value and must
  enter review notes. Changed punch calculations reset approvals. There is no payroll posting or
  automatic approval.
- Bulk approval is available at `/app/ag-ot-approval`. A Supervisor sees and approves only
  Agency Workers whose Supervisors table includes their current User. The server repeats this ownership
  check for every selected row; budget_location permissions also apply. System Manager is the backup approver
  and can handle all workers when the assigned supervisor is unavailable.
  Actions support approve planned, approve actual, custom values and reject, all with mandatory remarks.
  Approved and rejected rows are immutable and show approver login ID, approval timestamp and remarks.
- A duty with no planned or actual OT is marked Not Required, so it never appears as pending OT.
- `Agency Monthly Attendance` is the payout review matrix for the previous month's 26th through the
  selected month's 25th. Each date shows P, HD, A, OFF, H, PD or NR. The cell receives `✓`
  automatically only when no attendance, punch-review or OT action remains pending. HR Head monitors
  this matrix and does not approve individual rows. Agency, Department and Supervisor filters are available.
- Roster edits recalculate affected adjacent days. Historical reports reference current roster;
  Frappe track_changes preserves change history.
- Weekly-off punches are flagged for review; replacement off/OT treatment is not automatic.
- Raw punches cannot be edited/deleted through ordinary document operations.
- Separate punch log list captures unrostered events; they are processed when a roster is added.
- Temple devotees and final dashboard are outside this initial implementation.

## Validation before production

Simulator boundary tests cover roles, one-worker batches, branches, future dates and multi-date overnight input.
Local pure engine tests cover overnight, missing punches, open capture windows, boundary ambiguity,
separated multiple shifts, duplicate timestamps, breaks and OT. Python/JSON/JS syntax is checked.
No live Frappe bench was available during development. On staging, verify migration, roles,
Budget Location restrictions, form save, API authentication, retries, concurrent events, monthly page,
background worker processing and report with your installed v15 patch version.


