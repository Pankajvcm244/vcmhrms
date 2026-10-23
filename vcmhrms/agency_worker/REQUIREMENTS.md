# Agency Worker Attendance Requirements

## 1. Purpose

This document is the functional baseline for Agency Worker roster, biometric punch,
attendance, exception, overtime, holiday, monthly review, and period-lock features.
Changes to the module must preserve these rules unless this document and the related
acceptance tests are intentionally updated together.

The Agency Worker module is independent from the regular HR/Payroll employee
attendance flow. Agency records, shifts, rosters, punches, attendance, approvals,
holidays, and locks must not create or modify regular Employee, Employee Checkin,
Attendance, Shift Type, or Holiday List records.

## 2. Terminology

- **Agency Staff Name**: the person's display name.
- **Agency Staff ID**: the agency's manually maintained identifier. It is distinct
  from the biometric code.
- **Biometric Code**: the identifier used by the biometric provider.
- **Agency**: the external organization supplying agency staff.
- **Roster Manager**: a user mapped to an individual Agency Worker who may create or
  update that worker's roster.
- **Supervisor**: a user mapped to an individual Agency Worker who may review that
  worker's punch exceptions and approve or reject overtime.
- **Payroll month**: the attendance period from the 26th of the previous calendar
  month through the 25th of the selected month, inclusive.

## 3. Separation from regular HRMS

1. Agency staff must use the `Agency Worker` DocType and must not require an Employee
   record.
2. Agency holidays must use `Agency Holiday List` and its `Agency Holiday` child
   rows. The regular HRMS `Holiday List` and `Holiday` DocTypes must not be queried or
   modified by this module.
3. Agency punches must use `Agency Punch`; regular `Employee Checkin` records must
   not be created.
4. Agency attendance must use `Agency Attendance`; regular `Attendance` records must
   not be created.
5. Agency payroll review reports are informational inputs for payout preparation.
   They must not alter regular payroll documents.

## 4. Roles and authorization

### 4.1 System Manager

- Acts as an operational backup.
- May configure Agency master data, workers, shifts, rosters, attendance reviews,
  OT approvals, holiday lists, and locks.
- Must still be subject to locked-period immutability.

### 4.2 Agency Worker Manager

- May read, create, and maintain Agency, Agency Holiday List, Agency Worker, and
  Agency Shift master data.
- May run manual attendance finalization and lock a completed period.
- May review Monthly Attendance for all workers and
  filter it by Supervisor, Agency, and Department.
- This role does not replace worker-specific roster or supervisor mappings where a
  more restricted role is being used.

### 4.3 Agency Roster Manager

- May read only Agency Workers to whom the user is mapped.
- May create, replace, or update rosters only for Agency Workers to whom the user is
  mapped in `Agency Worker Roster Manager`.
- May use single-worker allocation, bulk simple-shift allocation, weekly-off
  allocation, and detailed daily roster editing for mapped workers.
- Must not approve OT or punch exceptions solely because of this role.

### 4.4 Agency Worker Supervisor

- May read only Agency Workers to whom the user is mapped.
- May view and act on punch exceptions and OT only for Agency Workers to whom the
  user is mapped in `Agency Worker Supervisor`.
- Multiple supervisors may be mapped to the same Agency Worker.
- Must not approve or edit another supervisor's unmapped workers by changing a page
  or report filter.
- Must not edit shift definitions or rosters solely because of this role.

### 4.5 Agency Worker Integration

- Is a non-Desk integration role.
- May call the biometric punch endpoint.
- Must not gain roster, attendance review, OT approval, master-data, report, or
  period-lock access from this role.

### 4.6 ERP Manager

- Has full read, create, update, delete, report, export, print, email, and share access
  to Agency Worker records.

### 4.7 Data import

- Every non-child Agency Worker DocType supports Data Import. Role permissions still
  control which users may create or update each DocType.

### 4.8 Budget Location

- Budget Location is operational data and a report/roster filter. Agency Worker access
  does not require a Budget Location User Permission.
- `VRN` is the default Budget Location in user-facing Agency forms and pages.
- Server-side checks are mandatory; hiding records or buttons in JavaScript is not
  sufficient authorization.

## 5. Master data

### 5.1 Agency

- Stores Agency Name, Agency Holiday List, Holiday Colour, and Disabled status.
- It must not store Budget Location, Department, or supervisor mappings.
- A disabled holiday list must not be selectable for new configuration or applied
  to reports.

### 5.2 Agency Worker

- Uses the label **Agency Staff Name**.
- Stores unique Agency Staff ID and unique Biometric Code as separate values.
- Stores Agency, Role, Department, Budget Location, active dates, status, multiple
  roster managers, and multiple supervisors.
- Engagement dates constrain roster creation and accepted punch dates.

### 5.3 Agency Shift

- Stores start/end time, required hours, configurable half-day hours, break minutes,
  punch-capture limits, and late/early grace minutes.
- `Half Day Hours` must be used directly. Falling back to half of Required Hours is
  allowed only when Half Day Hours is blank or zero.
- Only Agency Worker Manager and System Manager should change shift definitions.

### 5.4 Agency Holiday List

- Contains dated `Agency Holiday` child rows with a description.
- Duplicate dates in one list are rejected.
- A list can be shared by multiple agencies.
- Each Agency selects its list independently and defines its holiday display colour.

## 6. Document naming

- Agency Roster: `AGENCYROST-YYMM-#####`.
- Agency Attendance: `AGENCYATT-YYMM-######`.
- Agency Punch: `AgencyPunch-YYMMDD-######`.
- Names must not expose Agency Staff ID or Biometric Code.
- The sequence suffix must prevent collisions when many records are created for the
  same date or month.

## 7. Roster rules

1. Only one Agency Roster may exist for a worker and roster date.
2. A Duty roster must have at least one duty. A Weekly Off roster must have no duty.
3. A duty starts on the roster date, ends after it starts, and lasts no more than 24
   hours. Overnight duties may end on the following date.
4. Multiple duties are allowed on one day when their duty and planned-OT windows do
   not overlap.
5. Planned OT is stored separately as OT Before Shift and OT After Shift and requires
   an OT reason when either value is greater than zero.
6. Bulk simple-shift assignment supports up to 200 selected workers and a range of up
   to 62 days.
7. `Skip Existing` preserves existing detailed, multiple-shift, and OT allocations.
8. `Replace Existing` intentionally replaces the selected dates, including existing
   detailed allocations.
9. A configured weekly-off weekday creates Weekly Off rosters for that weekday.
10. Bulk completion must report created, replaced, skipped, and failed worker counts.
11. Roster changes must recalculate the affected roster and neighboring dates needed
    for overnight punch matching.

## 8. Biometric punch API

### 8.1 Endpoint and authentication

- Endpoint: `POST /api/method/vcmhrms.agency_worker.api.receive_punches`.
- Authentication uses a Frappe API key and secret belonging to a user with only the
  `Agency Worker Integration` role where practical.
- The endpoint accepts JSON and a maximum of 500 punches per request.

### 8.2 Payload

```json
{
  "punches": [
    {
      "biometric_code": "990001",
      "punch_time": "2026-10-11T08:45:00",
      "device_id": "DEVICE-1",
      "location": "VRN"
    }
  ]
}
```

- `biometric_code` and `punch_time` are required. The API resolves the Agency Worker
  from the globally unique Biometric Code; vendors do not send Staff ID.
- For the India-only rollout, an offset-free timestamp is interpreted in the site's
  configured Asia/Kolkata timezone.
- ISO 8601 timestamps carrying an offset are converted into the site timezone.
- Future punches and punches outside engagement dates are rejected.
- `device_id` and `location` are optional metadata.

### 8.3 Idempotency and immutability

- Event identity is derived from Biometric Code and normalized punch time.
- Retrying the same punch returns `duplicate` and must not create another record.
- Original punches are directionless, immutable, and cannot be edited or deleted.
- Old punches may arrive late and must recalculate nearby unlocked rosters.
- A punch whose date affects a locked attendance period must be rejected and must not
  create or change any record in that locked period.
- Each row returns `created`, `duplicate`, or `failed`; one failed row must not roll
  back valid rows in the same request.

## 9. Attendance calculation

1. Punches are matched to duty capture windows, including before-shift and after-shift
   OT capture limits.
2. Adjacent/overlapping duty capture windows are split so a punch is not silently
   consumed by the wrong duty.
3. First and last matched punches form the inferred pair. Original punches remain
   unchanged.
4. One punch after the capture window closes becomes `Needs Review`.
5. No punch after the capture window closes becomes `Absent`.
6. Before the capture window closes, incomplete attendance remains `Pending`.
7. Worked Hours equals elapsed time from first punch to last punch minus configured
   break minutes.
8. Worked Hours at or above Required Hours is `Present`.
9. Worked Hours below Required Hours but at or above Half Day Hours is `Half Day`.
10. Worked Hours below Half Day Hours is `Absent`.
11. Late Entry is set only when First In is later than shift start plus the configured
    grace minutes.
12. Early Exit is set only when Last Out is earlier than shift end minus the configured
    grace minutes.
13. Actual Before OT and Actual After OT are calculated separately.
14. A historical punch received later recalculates the affected date and may replace
    `Pending`, `Needs Review`, or calculated values while the period is unlocked.
15. A calculation change resets any affected OT approval so the supervisor reviews
    the new values.

## 10. Scheduled and manual processing

- Pending duties are finalized daily at 04:00 and 16:00 site time.
- The 16:00 run covers night shifts that finish after the 04:00 run while avoiding the
  13:00 peak period.
- Agency Worker Manager and System Manager have a manual finalization action for
  testing and operations.
- Manual and scheduled processing must call the same finalization logic.
- Processing must be idempotent and must skip locked periods.

## 11. Punch review and exceptions

- Late Entry, Early Exit, Half Day, Absent, and Needs Review require punch review.
- A mapped supervisor may acknowledge the calculated result with mandatory remarks.
- A mapped supervisor may enter a corrected first/last pair or a justified status
  override with a mandatory reason.
- Corrected punches are audit fields; original Agency Punch records are unchanged.
- Approval records who acted, when, and the remarks.
- A reviewed punch exception becomes read-only through normal review actions.

## 12. Overtime approval

- OT approval is required only if planned or actual before/after OT is greater than
  zero. No-OT duties use `Not Required`, never `Pending`.
- The approval page shows Agency Staff Name, Agency Staff ID, supervisor name, date,
  shift, planned/actual/approved before OT, planned/actual/approved after OT, reason,
  status, approver, approval time, and remarks.
- Supervisors may approve planned OT capped by actual OT, approve actual OT, save a
  custom amount capped by actual OT, or reject it.
- Remarks are mandatory.
- Approved or rejected OT is read-only.
- Approval records the login ID and timestamp.
- System Manager remains an operational backup approver.

## 13. Monthly attendance report

- The month filter uses `MM-YYYY` and covers the 26th through the 25th.
- Filters include Budget Location, Agency, Department, and Supervisor.
- Agency Worker Manager and System Manager may select All or a specific Supervisor.
- If Agency Worker Supervisor is granted report access in the future, that role may
  view only workers mapped to the logged-in supervisor; request parameters must not
  expand its access.
- The grid shows Agency Staff Name, Agency Staff ID, Agency, Department, Budget
  Location, every period date with weekday, Present/Half Day/Absent totals, and
  approved OT.
- Codes are: `P` Present, `HD` Half Day, `A` Absent, `OFF` Weekly Off, `PD` Pending,
  `NR` Needs Review, `L` Late Entry, `E` Early Exit, and `H` Agency Holiday.
- Absent is displayed as `A`, without an exclamation mark.
- A tick (`✓`) appears only when the date has no pending attendance, punch review, or
  OT approval action.
- A holiday is highlighted using its Agency's Holiday Colour. Holiday highlighting
  must not hide roster/attendance status, approved OT, or a pending action.
- Present/Half Day/Absent and approved-OT totals must include all applicable duty
  results even when the date is also an agency holiday.
- HR Head uses this report only to verify completion; HR Head does not approve or
  correct records from the monthly report.

## 14. Roster-versus-attendance report

- Shows the roster allocation and calculated attendance for operational review.
- Time values use `HH:MM` format.
- OT columns are named `OT Before Shift` and `OT After Shift`.
- Worker-facing labels use Agency Staff Name and Agency Staff ID.

## 15. Period lock

1. A lock applies to one Budget Location and one completed 26th-to-25th period.
2. A period ending today or in the future cannot be locked.
3. A period with no Duty roster cannot be locked.
4. Every Duty roster must have attendance for every roster duty.
5. No duty may remain Pending or Needs Review.
6. No punch or OT approval may remain Pending.
7. After locking, no user, including Administrator/System Manager or integration API,
   may insert, edit, delete, correct, approve, reject, recalculate, or replace roster,
   punch, or attendance data affecting that period.
8. The lock records period, Budget Location, login ID, and timestamp.
9. Repeating a lock request for the same period is idempotent.

## 16. Audit and data integrity

- Calculated attendance fields are read-only and are changed only by the calculation
  service.
- Correction, approval, and lock actions store actor, timestamp, and reason/remarks.
- API ingestion, recalculation, approval, and bulk roster operations must be safe when
  retried.
- The unique worker/date roster constraint must exist at database level.
- Concurrent recalculation and approval operations must lock the affected document so
  one action cannot silently overwrite another.
- User-entered labels and remarks must be escaped when rendered in custom pages.

## 17. Minimum regression suite

Every change to `vcmhrms/agency_worker` should verify at least:

1. Normal day shift: on-time complete pair produces Present with no pending review.
2. Late arrival beyond grace sets Late Entry and punch approval Pending.
3. Early departure beyond grace sets Early Exit and punch approval Pending.
4. Worked hours between Half Day and Required Hours produces Half Day.
5. Worked hours below Half Day Hours produces Absent.
6. One punch finalizes as Needs Review; no punch finalizes as Absent.
7. Night shift matches next-day checkout and finalizes after its capture window.
8. Multiple duties on one day receive the correct punches without duplication.
9. Before-shift and after-shift OT calculate independently.
10. No OT produces `Not Required`; planned/actual OT produces Pending.
11. Planned, actual, custom, and reject OT decisions enforce actual-OT caps and become
    read-only.
12. Punch correction preserves originals and records its audit trail.
13. Late historical punch recalculates an unlocked attendance record.
14. Duplicate API retry creates no duplicate Agency Punch.
15. A maximum-size punch batch recalculates every affected roster through bounded
    background jobs; failure of one roster does not stop the remaining rosters.
16. Offset-free India time and offset-bearing ISO time normalize correctly.
17. Bulk roster assignment handles multiple workers, weekly offs, Skip Existing, and
    Replace Existing and reports failures.
18. Roster Manager and Supervisor cannot act on unmapped workers, including by
    changing request parameters or report filters.
19. Agency Worker access does not depend on Budget Location User Permission.
20. Agency holidays do not query regular HRMS Holiday List records.
21. A holiday date retains its real attendance/OT/pending state, receives the configured
    holiday colour, and does not display an `H` holiday marker.
22. The monthly report provides a Year selector containing the current year and roster
    data years, plus a January-to-December Month selector.
23. The monthly report uses the 26th-to-25th range, shows weekdays, totals statuses and
    OT correctly, and shows a tick only when no action is pending.
24. Attendance Review displays dates and the date part of punch times as `DD-MM-YYYY`.
25. Locking fails when any attendance, punch review, or OT review is incomplete.
26. After locking, UI actions, direct RPC calls, bulk jobs, recalculation, and biometric
    ingestion cannot change the period.
27. Scheduled and manual finalization produce the same idempotent result.

## 18. Initial agency-data import rules

- Agency Staff ID comes from the source employee code. It is independent from the
  Biometric Code and may contain letters, numbers, hyphens, underscores, or slashes.
- Biometric Code is mandatory, read-only, and assigned automatically on first save
  from one global concurrency-safe `9xxxxxx` series. Users and imports do not enter it.
- Budget Location defaults to `VRN` for the initial agency-worker import.
- Active From defaults to `2026-09-01` for the initial import when no worker-specific
  start date is supplied.
- Source `Supervisor/Roster Assign` people become Roster Managers. Each person must be
  resolved to an individual enabled Frappe User before assignment.
- Source `Approval Authority Admin` people become Supervisors responsible for OT and
  punch/mispunch approval. Each person must be resolved to an individual enabled
  Frappe User before assignment.
- A valid weekday is stored as Default Weekly Off. `N/A`, blank, and unknown values
  import as blank; obvious spelling errors such as `Satuarday` are normalized.
- Bulk simple-shift assignment uses each worker's Default Weekly Off unless the user
  selects a common weekday or explicitly selects No Weekly Off.

## 19. Current review baseline

Reviewed against `develop` commit `31326b2` on 2026-10-15.

The following gaps were found at that baseline. They are retained here as explicit
regression cases and must not be reintroduced:

1. Agency-holiday dates were rendered as `H ✓` before attendance rows were evaluated.
   This could hide Pending/Needs Review, punch approval, OT approval, status totals, and
   approved OT on a holiday.
2. The report treats Agency Worker Manager like an individual supervisor when the
   Supervisor filter is blank. Therefore the intended `blank = All` view can return no
   workers instead of all workers in the manager's permitted Budget Location.
3. Biometric ingestion can insert a punch dated inside a locked period. Recalculation
   skips the lock, but the immutable punch record is still added.
4. `Agency Worker Supervisor` currently has write/create permission on Agency Shift,
   which conflicts with the approval-only supervisor responsibility.
5. A disabled Agency Holiday List is still applied by the monthly report and can remain
   linked to an Agency.

