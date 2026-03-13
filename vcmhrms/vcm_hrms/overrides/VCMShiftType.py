# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import getdate, nowdate, add_days, now_datetime, get_datetime, cint, flt, today
import logging
from datetime import datetime, timedelta, time as pytime
from hrms.hr.doctype.shift_type.shift_type import ShiftType as HRMSShiftType
logger = logging.getLogger(__name__)
SANDWICH_LEAVE_TYPE = "Sandwich Absent"

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
        settings = frappe.get_cached_doc("VCM HR Settings")
        today_date = getdate(today())
        # shift configured start
        shift_start = getdate(self.process_attendance_after) if self.process_attendance_after else None
        # payroll lock boundary
        lock_date = settings.attendance_locked_upto
        lock_start = add_days(getdate(lock_date), 1) if lock_date else None

        # repair window
        repair_days = cint(getattr(settings, "repair_window_days", 3) or 0)
        repair_start = add_days(today_date, -repair_days) if repair_days else None
        # CRITICAL: do not allow repair to cross payroll lock
        # ensure repair window never crosses payroll lock
        if repair_start and lock_start and getdate(repair_start) < getdate(lock_start):
            repair_start = lock_start

        candidates = [getdate(d) for d in [shift_start, lock_start, repair_start] if d]
        # logger.debug(f"Computed for shift {self.name}: \n"
        #              f"    shift_start={shift_start} \n"
        #              f"    lock_start={lock_start} \n"
        #              f"    repair_start={repair_start}")  
        if candidates:
            # logger.debug(f"Effective checkin start date candidates for shift {self.name}: {candidates}, selected: {max(candidates)}")   
            return max(candidates)
        # safe fallback
        return add_days(today_date, -7)
       

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
                # logger.debug(f"Fallback query found {len(manual_logs)} checkins between {effective_start} and {last_sync}")
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
        Repair attendance when late punches arrive.
        Handles:
        - sandwich break
        - overtime on holiday/weekoff
        - device sync delay
        - manual checkin edits
        collect shifts
        finish employee loop
        rebuild shifts once
        """
        #  recurrsion lock  
        if getattr(frappe.flags, "vcm_repair_running", False):
            return
        frappe.flags.vcm_repair_running = True      
        # -------------------------------------------------------
        # Capture repair start time (important for incremental safety)
        # -------------------------------------------------------
        
        repair_start = now_datetime()
        locked_upto, repair_days, last_run = get_company_lock_and_windows()
        # logger.debug(f" locked_upto {locked_upto}, repair_days {repair_days}, last_run {last_run} ")
        today = getdate(nowdate())
        repair_floor = add_days(today, -repair_days)
        if locked_upto:
            repair_floor = max(repair_floor, add_days(locked_upto, 1))
            # logger.debug(f" Adjusted repair_floor based on lock: {repair_floor}")
         # incremental window
        from_dt = get_datetime(last_run) if last_run else add_days(repair_start, -1)
        # if self.name == "GENERAL9_30_to_6_00":
        #     logger.debug(f"reconcile_late_checkins checkins {from_dt} (repair {repair_floor}) shift {self.name}") 
        # Prevent parallel repair runs           # 
        shifts_to_rebuild = set()     
        try:
            # -------------------------------------------------------
            # Fetch late or modified checkins
            # -------------------------------------------------------
            checkins = frappe.get_all(
                "Employee Checkin",
                filters={
                    "modified": (">", from_dt),
                    "skip_auto_attendance": 0,
                    "time": (">=", repair_floor),
                    "offshift": 0,
                    # "shift": self.name,
                },
                fields=["employee", "time", "shift"],
                order_by="employee, time asc",
                limit_page_length=5000,
            )
            if self.name == "GENERAL9_30_to_6_00":
                logger.debug(f" reconcile_late_checkins  Found {(checkins)} checkins to reconcile for shift {self.name}")    
            if not checkins:
                return
            # -------------------------------------------------------
            # Build impacted employee-day buckets
            # -------------------------------------------------------
            buckets = set()
            for c in checkins:
                punch_dt = get_datetime(c.time)
                if not punch_dt:
                    continue
                if not c.shift:
                    adate = getdate(punch_dt)
                else:
                    shift = frappe.get_cached_doc("Shift Type", c.shift)
                    adate = _attendance_date_for_punch(shift, punch_dt)
                if locked_upto and adate <= locked_upto:
                    continue
                if adate < repair_floor:
                    continue
                buckets.add((c.employee, adate))
            if not buckets:
                return
            # -------------------------------------------------------
            # Build impacted employee-day buckets
            # -------------------------------------------------------
            for employee, adate in sorted(buckets):
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
                    ],
                    as_dict=True,
                )
                # ---------------------------------------------------
                # Case 1 — no attendance (holiday / weekoff)
                # ---------------------------------------------------
                if not attendance:
                    logger.debug(
                        f"[repair] no attendance employee={employee} date={adate}"
                    )
                    # unlink logs so HRMS can rebuild attendance
                    _unlink_day_checkins(employee, adate)

                    # find shift from checkins
                    shift_name = frappe.db.get_value(
                        "Employee Checkin",
                        {
                            "employee": employee,
                            "time": ("between", [adate, add_days(adate, 1)]),
                            "offshift": 0
                        },
                        "shift"
                    )
                    if shift_name:
                        shifts_to_rebuild.add(shift_name) 
                    continue
                # ---------------------------------------------------
                # Sandwich leave handling
                # ---------------------------------------------------
                if attendance.leave_type == SANDWICH_LEAVE_TYPE:
                    if attendance.leave_application:
                        try:
                            leave_doc = frappe.get_doc(
                                "Leave Application",
                                attendance.leave_application
                            )
                            if leave_doc.docstatus == 1:
                                leave_doc.cancel()
                                logger.debug(
                                    f"repair: cancelled sandwich leave {leave_doc.name} "
                                    f"employee={employee} date={adate}"
                                )
                        except Exception:
                            logger.exception("sandwich cancel failed")
                # ---------------------------------------------------
                # Cancel existing attendance
                # ---------------------------------------------------
                if attendance.docstatus == 1:
                    att_doc = frappe.get_doc("Attendance", attendance.name)
                    if att_doc.docstatus == 1:
                        att_doc.cancel()

                    logger.debug(
                        f"repair: cancelled attendance {attendance.name} "
                        f"employee={employee} date={adate}"
                    )
                # ---------------------------------------------------
                # Unlink checkins so HRMS recomputes
                # ---------------------------------------------------
                _unlink_day_checkins(employee, adate)
                if attendance.shift:
                    shifts_to_rebuild.add(attendance.shift)
            # -----------------------------------------------------
            # Rebuild attendance once per shift
            # -----------------------------------------------------
            for shift_name in shifts_to_rebuild:
                logger.debug(
                    f"[repair] rebuilding attendance for shift {shift_name}"
                )
                shift_doc = frappe.get_cached_doc("Shift Type", shift_name)
                shift_doc.process_auto_attendance()
        finally:
            # -------------------------------------------------------
            # Update repair timestamp safely
            # -------------------------------------------------------
            frappe.db.set_value(
                "VCM HR Settings",
                "VCM HR Settings",
                "last_repair_run_at",
                repair_start,
            )
            frappe.db.commit()
            #  release lock
            frappe.flags.vcm_repair_running = False      

    
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
        # logger.debug(f"[LEAVE-SYNC] No shift on Attendance and could not infer shift from checkins for {employee} {adate}")
        return False
    shift = frappe.get_cached_doc("Shift Type", shift_name)

     # ------------------------------------------------------------
    # 2) Compute valid scan window (overnight + grace)
    # ------------------------------------------------------------
    win_start, win_end = _shift_scan_window(shift, adate)
    # logger.debug(f"Computed scan window for leave-driven attendance sync for employee={employee} date={adate} shift={shift.name}: win_start={win_start} win_end={win_end}") 
    if not win_start or not win_end:
        return False
    # logger.debug(f"Syncing leave attendance for employee={employee} date={adate} using shift {shift.name} with scan window {win_start} to {win_end}")   
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
        # logger.debug(f"[LEAVE-SYNC] No valid checkins in window for {employee} {adate}")
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
    # logger.debug(
    #     f"[LEAVE-SYNC] {employee} {adate}: old_in={old_in} old_out={old_out} old_wh={old_wh} "
    #     f"new_in={in_dt} new_out={out_dt} new_wh={wh}"
    # )
    # ------------------------------------------------------------
    # 5) Update Attendance without changing leave status, we can not use save as doc is already submitted
    # ------------------------------------------------------------
    frappe.db.set_value(
        "Attendance",
        att["name"],
        {
            "in_time": in_dt,
            "out_time": out_dt,
            "working_hours": wh,
            "shift": shift.name,
        },
    )
    #  now update all flags
    att_doc = frappe.get_doc("Attendance", att["name"])
    # update memory object
    att_doc.in_time = in_dt
    att_doc.out_time = out_dt
    att_doc.working_hours = wh
    att_doc.shift = shift.name
    # run the same policy logic that validate() normally runs
    from vcmhrms.vcm_hrms.overrides.attendance_hooks import (
        apply_nonflex_strict_hours,
        enforce_late_entry_policy,
    )
    # logger.debug(f"Applying attendance policies for leave-driven attendance sync for {employee} {adate}")   
    apply_nonflex_strict_hours(att_doc)
    enforce_late_entry_policy(att_doc)
    frappe.db.set_value(
        "Attendance",
        att_doc.name,
        {
            "in_time": in_dt,
            "out_time": out_dt,
            "working_hours": wh,
            "shift": shift.name,
            "late_entry": att_doc.late_entry,
            "early_exit": att_doc.early_exit,
            "custom_late_entry_within_grace": att_doc.custom_late_entry_within_grace,
            "custom_first_half_done": att_doc.custom_first_half_done,
            "custom_second_half_done": att_doc.custom_second_half_done,
            "custom_sandwich_applied": att_doc.custom_sandwich_applied,
            "custom_created_by_sandwich": att_doc.custom_created_by_sandwich,
        },
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
    # roles = set(frappe.get_roles(frappe.session.user))
    # if "System Manager" not in roles and "HR Manager" not in roles:
    #     frappe.throw(("Not permitted."))
    details = {"started_at": str(now_datetime())}
    # import traceback

    # logger.debug(
    #     "run_auto_attendance_now called\n%s",
    #     "".join(traceback.format_stack())
    # )
    # logger.debug(f"Manual trigger for auto attendance by user {frappe.session.user} for employee {employee}")
    logging.debug(f"[run_auto_attendance_now] Manual trigger details: {details}")
    processed = 0
    errors = 0
    if employee:
        # Restrict work for testing: only process shifts where employee has an active Shift Assignment
        shift_names = frappe.get_all(
            "Shift Assignment",
            filters={"employee": employee, "docstatus": 1, "status": "Active"},
            pluck="shift_type",
        )        
        for s in set(shift_names):
            try:
                doc = frappe.get_cached_doc("Shift Type", s)
                doc.process_auto_attendance(is_manually_triggered=True)
                processed += 1
            except Exception:
                errors += 1
                frappe.log_error(title=f"Auto attendance failed shift={s}", message=frappe.get_traceback())
        details.update({
            "mode": "employee",
            "employee": employee,
            "shifts": list(set(shift_names)),
        })
        return {
            "ok": True,
            "processed": processed,
            "errors": errors,
            "details": details,
        }
    # Default: process all shifts (HRMS standard)
    from hrms.hr.doctype.shift_type.shift_type import process_auto_attendance_for_all_shifts
    # logger.debug("Running auto attendance for all shifts (manual trigger)")
    process_auto_attendance_for_all_shifts()
    processed = frappe.db.count(
        "Shift Type",
        {"enable_auto_attendance": 1}
    )
    details.update({"mode": "all_shifts"})
    return {
        "ok": True,
        "processed": processed,
        "errors": 0,
        "details": details,
    }

