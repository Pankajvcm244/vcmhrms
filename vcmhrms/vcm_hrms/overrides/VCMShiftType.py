# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import getdate, nowdate, add_days, now_datetime, get_datetime, cint, flt
import logging
from datetime import datetime, timedelta, time as pytime
from hrms.hr.doctype.shift_type.shift_type import ShiftType as HRMSShiftType
logger = logging.getLogger(__name__)

# Auto-attendance will only ever consider checkins from the current 
# payroll period (26th onward) (plus any HRMS “process_attendance_after” constraint).
# Repair_window_days (wider but controlled)
# Handles:
# ✅ Absent already marked
# ✅ edge corrections
# ✅ HR fixes
#  low frequency may be once a day but expensive
#  attendance_locked_upto  ← ERP System Manager controlled
# [Employee Checkin arrives]
#           ↓
# (lookback window applies here)
#           ↓
# [Auto Attendance creates Attendance]
#           ↓
# (Late punch discovered)
#           ↓
# (repair window applies here)
#           ↓
# [Attendance corrected]
#           ↓
# (payroll locked → freeze)

def get_company_lock_and_windows(_company=None):
    """
    Source of truth: Single doctype 'VCM HR Settings'
    Fields:
      - attendance_locked_upto (Date)
      - repair_window_days (Int)
      - last_repair_run_at (Datetime)
    """
    settings = frappe.get_cached_doc("VCM HR Settings")  # cached, fast
    locked_upto = getdate(settings.attendance_locked_upto) if settings.attendance_locked_upto else None
    repair_days = int(getattr(settings, "repair_window_days", 10) or 10)
    last_repair_run_at = get_datetime(settings.last_repair_run_at) if settings.last_repair_run_at else None     
    return locked_upto, repair_days, last_repair_run_at


class VCMShiftType(HRMSShiftType):
    # -----------------------------
    # Window 1: lookback for checkins
    # -----------------------------
    def get_effective_checkin_start_date(self):
        """
            Routine scan lower bound:
            max( locked_upto + 1, today - repair_days, process_attendance_after )
        """
        # logger.debug(f" in get_effective_checkin_start_date")       
        locked_upto, repair_days, _last_run = get_company_lock_and_windows()
        start = add_days(getdate(nowdate()), -repair_days)
        if locked_upto:
            lock_start = add_days(locked_upto, 1)
            if lock_start > start:
                start = lock_start
        # Also respect HRMS process_attendance_after (if newer)
        if self.process_attendance_after:
            pa = getdate(self.process_attendance_after)
            if pa > start:
                start = pa
        return start

    def get_employee_checkins(self) -> list[dict]:
        """
        HRMS v15 signature-compatible override.
        We clamp the scan window by temporarily pushing process_attendance_after forward
        to our effective start date. HRMS base implementation already uses
        self.process_attendance_after internally.
        - CRITICAL: Always return a list (never None), else HRMS _process() crashes.
        """
        effective_start = self.get_effective_checkin_start_date()        
        original = self.process_attendance_after
        # logger.debug(f" effective start {effective_start}, origional {original}")
        try:
            if not original or getdate(original) < effective_start:
                self.process_attendance_after = effective_start
            logs = super().get_employee_checkins()
            if logs:
                return logs
            # Fallback only if HRMS returns nothing (keeps you close to standard)
            last_sync = self.last_sync_of_checkin or now_datetime()
            # logger.debug(f"[VCM] HRMS get_employee_checkins shift {self.name} returned 0; using fallback query")

            manual_logs =  frappe.get_all(
                "Employee Checkin",
                fields=[
                    "name","employee","log_type","time","shift",
                    "shift_start","shift_end","shift_actual_start","shift_actual_end","device_id",
                ],
                filters={
                    "skip_auto_attendance": 0,
                    "attendance": ("is", "not set"),
                    "time": ("between", [get_datetime(effective_start), get_datetime(last_sync)]),
                    "offshift": 0,
                    "shift": self.name,
                },
                order_by="employee,time",
                limit_page_length=5000,
            )
            if manual_logs:
                logger.debug(f"Fallback query found {len(manual_logs)} checkins between {effective_start} and {last_sync}")
                return manual_logs or []
            # 4) CRITICAL: return [] if nothing found (never None)
            return []
        finally:
            self.process_attendance_after = original

    # ---------------------------------------
    # Window 2: repair window for late arrivals
    #  ----------------------------------------
    # bench --site pankaj.vcmerp.in console
    """ 
    import frappe
    shift = frappe.get_doc("Shift Type", "GENERAL9_30_to_6_00")
    shift.process_auto_attendance()
    """ 
 
    def process_auto_attendance(self, is_manually_triggered=False):
        # logger.debug("in process_auto_attendance (forcing safe non-manual mode)")
        # Then do targeted repair for late-arriving punches,  
        # Unlink/cancel impacted days first, so HRMS can recreate them cleanly on next run.
        self.reconcile_late_checkins()
        # Run standard HRMS auto attendance (creates/updates based on unlinked checkins)
         # 2) Now HRMS sees those checkins as unlinked and will recreate attendance immediately
        super().process_auto_attendance(is_manually_triggered=False)
        

    def reconcile_late_checkins(self):
        """
        Goal: If new punches arrive late (device sync delay / manual entry),
        re-run HRMS attendance calculation for only the impacted employee-days.

        Strategy (delta-based):
        1) Fetch checkins modified since last_repair_run_at (fast, incremental).
        2) Convert each checkin to the correct attendance_date based on its Shift Type window
        (handles overnight shifts correctly).
        3) For each (employee, attendance_date):
            A) If attendance is leave-driven (Leave Application / On Leave):
            - DO NOT cancel / recreate attendance
            - Update in_time/out_time/working_hours and relink checkins to that Attendance
            B) Else (normal absent/half-day/present etc):
            - Cancel existing submitted attendance (docstatus=1) so HRMS can recreate cleanly
            - Unlink that day’s checkins (attendance=NULL) so HRMS picks them up again
        Notes:
        - We never touch locked dates.
        - We do NOT skip Present anymore. Late punches can upgrade Half Day/Absent -> Present,
        and can also adjust working hours / in-out times for Present.
        HRMS ShiftType.process_auto_attendance() creates (or recreates) Attendance, because HRMS only 
            processes unlinked checkins (attendance is null) when it runs auto-attendance.
        DEBUG in console 
            shift = frappe.get_doc("Shift Type", "GENERAL9_30_to_6_00")
            shift.process_auto_attendance()
        """
        # logger.debug(f" in reconcile_late_checkins ")
        locked_upto, repair_days, last_run = get_company_lock_and_windows()
        # logger.debug(f" locked_upto {locked_upto}, repair_days {repair_days}, last_run {last_run} ")
        today = getdate(nowdate())
        repair_floor = add_days(today, -repair_days)
        if locked_upto:
            repair_floor = max(repair_floor, add_days(locked_upto, 1))
        # First run safety: only look at last 1 day to to handle Null Last_run
        from_dt = get_datetime(last_run) if last_run else add_days(now_datetime(), -1)
         # Advance last run immediately (prevents reprocessing the same window)
        frappe.db.set_value("VCM HR Settings", "VCM HR Settings", "last_repair_run_at", now_datetime())
        # Fetch checkins changed since last run
        checkins = frappe.get_all(
            "Employee Checkin",
            filters={
                "modified": (">=", from_dt),
                "skip_auto_attendance": 0,
                "time": (">=", repair_floor),
                "offshift": 0,  # IMPORTANT: ignore outside-window punches (your validate hook should set this)
            },
            fields=["employee", "time", "shift"],
            order_by="employee, time asc",
            limit_page_length=5000,  # safety cap; adjust if needed
        )
        if not checkins:
            # logger.debug("No checkins since last repair run")
            frappe.db.commit()
            return
        logger.debug(f"checkins_since_last_run count={len(checkins)} from_dt={from_dt} sample={checkins[:4]}")
         # Build (employee, attendance_date) buckets correctly (midnight-safe)
        buckets = set()
        for c in checkins:
            punch_dt = get_datetime(c.time)
            if not punch_dt:
                continue
            # If shift is missing, fallback to calendar date (best-effort)
            if not c.shift:
                adate = getdate(punch_dt)
            else:
                shift = frappe.get_cached_doc("Shift Type", c.shift)
                adate = _attendance_date_for_punch(shift, punch_dt)  # midnight-safe mapping

            # hard lock: never touch locked or earlier
            if locked_upto and adate <= locked_upto:
                continue
            # repair window: don’t reopen very old dates
            if adate < repair_floor:
                continue
            buckets.add((c.employee, adate))
        if not buckets:
            # logger.debug("No buckets to reconcile")
            frappe.db.commit()
            return        
        # logger.debug(f"[REPAIR] Buckets to reconcile: {buckets}")
         # -----------------------------------------------------------------
        # Process each employee-day
        # -----------------------------------------------------------------
        for employee, adate in sorted(buckets):
            # logger.debug(f"Reconciling attendance for employee={employee} date={adate}")
            # If Attendance doesn't exist, skip (conservative).
            # If you want: create attendance from logs here later.
            attendance = frappe.get_value(
                "Attendance",
                {"employee": employee, "attendance_date": adate, "docstatus": ("<", 2)},
                [
                    "name",
                    "status",
                    "docstatus",
                    "shift",
                    "leave_application",
                    "leave_type",
                    "half_day_status",
                    # "in_time",        # optional but useful for debug/compare
                    # "out_time",       # optional
                    # "working_hours",  # optional
                ],
                as_dict=True,
            )
             # If no attendance exists, we typically do nothing here:
            # HRMS will create it when it scans unlinked logs in process_auto_attendance()
            # BUT we still want to ensure logs are unlinked, so HRMS can create it.
            if not attendance:
                # Unlink checkins for this employee-day window so HRMS can create Attendance
                _unlink_day_checkins(employee, adate)
                continue
            # logger.debug(f" attendance  {attendance}, status {attendance.status}")
            # never touch Present
            status_lc = (attendance.status or "").strip().lower()
            half_day_status_lc = (attendance.half_day_status or "").strip().lower()
            # Never touch Present
            if status_lc == "present":
                continue
            # -------------------------------------------------------------
            # LEAVE-DRIVEN ATTENDANCE
            # -------------------------------------------------------------
            is_leave_driven = bool(
                attendance.leave_application
                or attendance.leave_type
                or status_lc == "on leave"
                or (status_lc == "half day" and half_day_status_lc in ("on leave", "leave"))
            )
            logger.debug(f" employee={employee} date={adate} is_leave_driven={is_leave_driven} status={attendance.status} half_day_status={attendance.half_day_status}")    
            if is_leave_driven:
                 # Keep the leave status, but update times/hours and link logs
                changed = _sync_leave_attendance_times(attendance, employee, adate)
                logger.debug(f"[LEAVE] sync employee={employee} date={adate} att={attendance.name} changed={changed}")
                continue
            # -------------------------------------------------------------
            # NORMAL ATTENDANCE → let HRMS recompute
            # -------------------------------------------------------------
            logger.debug( f"[REPAIR] employee={employee} date={adate} att={attendance.name}")        

            # Cancel submitted attendance (Absent / Half Day etc.)
            if attendance.docstatus == 1:
                frappe.get_doc("Attendance", attendance.name).cancel()
                # logger.debug(f"cancelling  {attendance.name}")
            # Unlink day’s checkins so HRMS reprocesses them in the subsequent super().process_auto_attendance()
            _unlink_day_checkins(employee, adate)  
        frappe.db.commit()

    

    
    # Override has_incorrect_shift_config() to allow running
    # Keep the safety checks, but don’t block just because last_sync_of_checkin is empty./
    def has_incorrect_shift_config(self):
        # logger.debug(
        #     f"[VCM] has_incorrect_shift_config shift={self.name} "
        #     f"enable_auto_attendance={self.enable_auto_attendance} "
        #     f"process_attendance_after={self.process_attendance_after} "
        #     f"last_sync_of_checkin={self.last_sync_of_checkin}"
        # )
        # Must be enabled
        if not cint(self.enable_auto_attendance):
            return True
        # Ensure process_attendance_after is set (we can safely default it)
        if not self.process_attendance_after:
            # Use your effective start date window (lookback + lock)
            self.process_attendance_after = self.get_effective_checkin_start_date()
        # last_sync_of_checkin is used by HRMS to limit scanning.
        # If missing, set it to now so HRMS does not refuse to run.
        if not self.last_sync_of_checkin:
            self.last_sync_of_checkin = now_datetime()
        return False


def _unlink_day_checkins(employee: str, attendance_date):
        """
        Unlink only the checkins that belong to this attendance_date.
        For midnight shifts, this must be based on shift windows, not DATE(time).
        Best-effort approach:
        - Unlink all checkins for that employee where time is within [adate 00:00, adate+1 23:59]
        - AND offshift=0 (valid punches only)
        If you want perfect accuracy per shift, we can extend this to per-shift windows.
        """
        day_start = get_datetime(f"{attendance_date} 00:00:00")
        day_end = get_datetime(f"{add_days(attendance_date, 1)} 00:00:00")

        frappe.db.sql(
            """
            UPDATE `tabEmployee Checkin`
            SET attendance = NULL
            WHERE employee = %s
            AND skip_auto_attendance = 0
            AND offshift = 0
            AND `time` >= %s AND `time` < %s
            """,
            (employee, day_start, day_end),
        )

def _attendance_date_for_punch(shift, punch_dt):
    """
    Determine correct attendance_date for a punch based on shift window.
    Works for overnight shifts.
    """
    d = getdate(punch_dt)

    w1s, w1e = _shift_scan_window(shift, d)
    if w1s and w1e and w1s <= punch_dt <= w1e:
        return d

    prev = add_days(d, -1)
    w2s, w2e = _shift_scan_window(shift, prev)
    if w2s and w2e and w2s <= punch_dt <= w2e:
        return prev

    return d

def _to_time(t):
    """
    Convert Shift Type time field into python time.
    HRMS sometimes stores:
    - datetime.time (e.g. 09:30:00)
    - timedelta (e.g. 9:30:00 as a duration from midnight)
    - string (e.g. "09:30:00")
    This helper normalizes safely.
    """
    if t is None:
        return None
    if isinstance(t, pytime):
        return t
    if isinstance(t, timedelta):
        total = int(t.total_seconds()) % 86400
        return pytime(hour=total // 3600, minute=(total % 3600) // 60, second=total % 60)
    # "09:30:00" fallback
    parts = str(t).split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        return pytime(hour=h, minute=m, second=s)
    except Exception:
        return None

def _shift_base_bounds(shift, attendance_date):
    """
    Compute base shift start/end WITHOUT grace.
    Also handles overnight shifts:
    Example: 22:00 → 06:00
    """
    st = _to_time(getattr(shift, "start_time", None))
    et = _to_time(getattr(shift, "end_time", None))
    if not st or not et:
        return None, None
    d = getdate(attendance_date)
    start_dt = datetime.combine(d, st)
    end_dt = datetime.combine(d, et)
    # Overnight shift support
    if end_dt <= start_dt:
        end_dt = end_dt + timedelta(days=1)
    return start_dt, end_dt


def _get_grace_minutes(shift):
    """
    Read grace minutes from Shift Type.

    IMPORTANT:
    Field names may vary slightly across HRMS versions.
    Adjust here ONLY if your fieldnames differ.
    """
    pre = (getattr(shift, "begin_check_in_before_shift_start_time", None)  or 0 )
    post = (getattr(shift, "allow_check_out_after_shift_end_time", None) or 0 )
    # logger.debug(f"Grace minutes for shift {shift.name}: pre={pre} post={post}")
    try:
        return int(pre or 0), int(post or 0)
    except Exception:
        return 0, 0

def _shift_scan_window(shift, attendance_date):
    """
    FINAL VALID WINDOW used for:
    ✓ auto attendance
    ✓ leave attendance repair
    ✓ late punch handling
    Window = shift bounds ± grace minutes
    """
    start_dt, end_dt = _shift_base_bounds(shift, attendance_date)
    if not start_dt or not end_dt:
        return None, None
    pre_mins, post_mins = _get_grace_minutes(shift)
    # logger.debug(f"Shift scan window for shift {shift.name}: start={start_dt} end={end_dt} pre={pre_mins} post={post_mins}")    
    return (
        start_dt - timedelta(minutes=pre_mins),
        end_dt + timedelta(minutes=post_mins),
    )

# When attendance is created via Leave Application:
# ❌ DO NOT cancel attendance
# ❌ DO NOT recreate attendance
# ✅ Update in_time/out_time/working_hours
# ✅ Link valid checkins
def _sync_leave_attendance_times(att, employee: str, adate) -> bool:
    """
    Update leave-driven Attendance using real punches.
    Returns True if anything changed.
    """
    # ------------------------------------------------------------
    # 1) Determine shift for this day
    # ------------------------------------------------------------
    shift_name = att.get("shift")
    # If Attendance has no shift (can happen), try to infer from checkins first
    if not shift_name:
        inferred = frappe.get_value(
            "Employee Checkin",
            {"employee": employee, "skip_auto_attendance": 0, "offshift": 0, "time": ("between", [adate, add_days(adate, 1)])},
            "shift",
        )
        if inferred:
            shift_name = inferred
    # If still missing, we cannot compute window safely
    if not shift_name:
        logger.debug(f"[LEAVE-SYNC] No shift on Attendance and could not infer shift from checkins for {employee} {adate}")
        return False
    shift = frappe.get_cached_doc("Shift Type", shift_name)

     # ------------------------------------------------------------
    # 2) Compute valid scan window (overnight + grace)
    # ------------------------------------------------------------
    win_start, win_end = _shift_scan_window(shift, adate)
    logger.debug(f"Computed scan window for leave-driven attendance sync for employee={employee} date={adate} shift={shift.name}: win_start={win_start} win_end={win_end}") 
    if not win_start or not win_end:
        return False
    logger.debug(f"Syncing leave attendance for employee={employee} date={adate} using shift {shift.name} with scan window {win_start} to {win_end}")   
    # ------------------------------------------------------------
    # 3) Fetch valid checkins within window
    # ------------------------------------------------------------
    logs = frappe.get_all(
        "Employee Checkin",
        filters={
            "employee": employee,
            "skip_auto_attendance": 0,
            "offshift": 0,
            "time": ("between", [win_start, win_end]),
        },
        fields=["name", "time", "shift"],
        order_by="time asc",
        limit_page_length=5000,
    )
    if not logs:
        logger.debug(f"[LEAVE-SYNC] No valid checkins in window for {employee} {adate}")
        return False
    # Prefer logs mapped to same shift (keeps you close to HRMS)
    same_shift = [l for l in logs if l.get("shift") == shift.name]
    if same_shift:
        logs = same_shift
    # ------------------------------------------------------------
    # 4) Compute day in/out and working hours from those logs
    # ------------------------------------------------------------
    in_dt = min(get_datetime(x.time) for x in logs)
    out_dt = max(get_datetime(x.time) for x in logs)
    seconds = max(0, (out_dt - in_dt).total_seconds())
    wh = flt(seconds / 3600.0, 2)
    old_in = att.get("in_time")
    old_out = att.get("out_time")
    old_wh = att.get("working_hours")
    logger.debug(
        f"[LEAVE-SYNC] {employee} {adate}: old_in={old_in} old_out={old_out} old_wh={old_wh} "
        f"new_in={in_dt} new_out={out_dt} new_wh={wh}"
    )
    # ------------------------------------------------------------
    # 5) Update Attendance without changing leave status
    # ------------------------------------------------------------
    frappe.db.set_value(
        "Attendance",
        att["name"],
        {"in_time": in_dt, "out_time": out_dt, "working_hours": wh, "shift": shift.name},
    )
    # ------------------------------------------------------------
    # 6) Link only these logs to the existing leave Attendance
    # ------------------------------------------------------------
    names = [l["name"] for l in logs]
    frappe.db.sql(
        f"""
        UPDATE `tabEmployee Checkin`
        SET attendance = %s
        WHERE name IN ({",".join(["%s"] * len(names))})
        """,
        tuple([att["name"]] + names),
    )
    return True


@frappe.whitelist()
def run_auto_attendance_now(employee: str | None = None):
    """
    Manual trigger for testers.

    - Uses HRMS entrypoint when employee is not provided (upgrade-safe).
    - When employee is provided, processes only shifts that employee belongs to (faster for testing).
    """
    roles = set(frappe.get_roles(frappe.session.user))
    if "System Manager" not in roles and "HR Manager" not in roles:
        frappe.throw(_("Not permitted."))
    details = {"started_at": str(now_datetime())}
    if employee:
        # Restrict work for testing: only process shifts where employee has an active Shift Assignment
        shift_names = frappe.get_all(
            "Shift Assignment",
            filters={"employee": employee, "docstatus": 1, "status": "Active"},
            pluck="shift_type",
        )
        processed = 0
        errors = 0
        for s in set(shift_names):
            try:
                doc = frappe.get_cached_doc("Shift Type", s)
                doc.process_auto_attendance(is_manually_triggered=True)
                processed += 1
            except Exception:
                errors += 1
                frappe.log_error(title=f"Auto attendance failed shift={s}", message=frappe.get_traceback())
        details["mode"] = "employee"
        details["employee"] = employee
        details["shifts"] = list(set(shift_names))
        return {"processed": processed, "errors": errors, "details": details}
    # Default: process all shifts (HRMS standard)
    from hrms.hr.doctype.shift_type.shift_type import process_auto_attendance_for_all_shifts
    process_auto_attendance_for_all_shifts()
    details["mode"] = "all_shifts"
    return {
        "processed": frappe.db.count("Shift Type", {"enable_auto_attendance": 1}),
        "errors": 0,
        "details": details,
    }

