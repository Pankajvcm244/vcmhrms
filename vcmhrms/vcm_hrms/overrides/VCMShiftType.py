# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import getdate, nowdate, add_days, now_datetime, get_datetime, cint, flt, today
import logging
import random
from datetime import datetime, timedelta, time as pytime
from hrms.hr.doctype.shift_type.shift_type import ShiftType as HRMSShiftType
from vcmhrms.vcm_hrms.overrides.attendance_hooks import apply_nonflex_strict_hours
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
    last_repair_run_at = get_datetime(settings.last_repair_run_at) if settings.last_repair_run_at else None     
    return locked_upto, last_repair_run_at


class VCMShiftType(HRMSShiftType):
    # -----------------------------
    # Window 1: lookback for checkins
    # -----------------------------
    def get_effective_checkin_start_date(self):
        """
            Routine scan lower bound:
            max( locked_upto + 1, process_attendance_after )
        """
        # logger.debug(f" in get_effective_checkin_start_date") 
        settings = frappe.get_cached_doc("VCM HR Settings")
        today_date = getdate(today())
        # shift configured start
        shift_start = getdate(self.process_attendance_after) if self.process_attendance_after else None
        # payroll lock boundary
        lock_date = settings.attendance_locked_upto
        lock_start = add_days(getdate(lock_date), 1) if lock_date else None

        candidates = [getdate(d) for d in [shift_start, lock_start] if d]
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
            last_sync = now_datetime()
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

    def reconcile_late_checkins(self, repair_start=None):       
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
        if not repair_start:
            repair_start = now_datetime()
        locked_upto, last_run = get_company_lock_and_windows()
        # if self.name == "GENERAL9_30_to_6_00":
        # logger.debug(f" shift {self.name} ,locked_upto {locked_upto}, repair_start {repair_start}, last_run {last_run} ")       
        repair_floor = None
        if locked_upto:
            repair_floor = add_days(locked_upto, 1)
            # logger.debug(f" Adjusted repair_floor based on lock: {repair_floor}")
         # incremental window
        from_dt = get_datetime(last_run) if last_run else add_days(repair_start, -1)
        # if self.name == "GENERAL9_30_to_6_00":
        #     logger.debug(f"reconcile_late_checkins 1 checkins {from_dt} (repair {repair_floor}) shift {self.name}") 
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
                    "time": (">=", repair_floor),
                    "offshift": 0,
                    "shift": self.name,
                },
                fields=["employee", "time", "shift"],
                order_by="employee, time asc",
                limit_page_length=5000,
            )
            if self.name == "Flexy":
                logger.debug(f"reconcile_late_checkins checkins {(checkins)} , to reconcile for shift {self.name}")    
            # if not checkins:
            #     # logger.debug(f"reconcile_late_checkins 0 no checkins to reconcile for shift {self.name}")
            #     # Dont return here we may have updated attendence.
            #     pass
            # -------------------------------------------------------
            # Build impacted employee-day buckets
            #  first add where attendance has changed.
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
                buckets.add((c.employee, adate, c.shift))
            # -------------------------------------------------------
            # Check Leave Update as we need to process them also  
            # -------------------------------------------------------
            leave_updates = frappe.get_all(
                "Leave Application",
                filters={
                    "modified": (">", from_dt),
                    "docstatus": ("<", 2)
                },
                fields=["employee", "from_date", "to_date", "half_day", "half_day_date"]
            )
            if self.name == "Flexy":
                logger.debug(f" reconcile_late_checkins 1-1 leave_updates {leave_updates} {from_dt} shift {self.name}") 
            for l in leave_updates:
                if l.half_day:
                    d = l.half_day_date
                    if not d:
                        continue
                    if locked_upto and d <= locked_upto:
                        continue
                    if repair_floor and d < repair_floor:
                        continue
                    buckets.add((l.employee, d))
                else:
                    for d in date_range(l.from_date, l.to_date):
                        if locked_upto and d <= locked_upto:
                            continue
                        if repair_floor and d < repair_floor:
                            continue
                        buckets.add((l.employee, d))
            # -------------------------------------------------------
            # Check Attendence Update as we need to process them also  
            # -------------------------------------------------------  
            # First extract impacted employees from attendance updates, then fetch all attendance updates for those employees to build buckets.
            #  This is to avoid fetching too many attendance records when we have many checkins but few attendances.
            employees = {c.employee for c in checkins}
            employees.update(l.employee for l in leave_updates)         
            # attendance_updates = frappe.get_all(
            #     "Attendance",
            #     filters={
            #         "modified": (">", from_dt),
            #         "docstatus": ("<", 2),
            #         "employee": ("in", list(employees)),  
            #         # "shift": self.name,
            #         "status": ["!=", "Absent"], 
            #     },
            #     fields=["employee", "attendance_date", "shift"],
            # )
            # if self.name == "Flexy":
            #     logger.debug(f" reconcile_late_checkins  attendence {(attendance_updates)} attendances updated since {from_dt} to reconcile for shift {self.name}")
            # for att in attendance_updates:
            #     # if self.name == "GENERAL9_30_to_6_00":
            #     #     logger.debug(f"reconcile_late_checkins 2 attendance update employee {att.employee} date {att.attendance_date} shift {att.shift}")
            #     if locked_upto and att.attendance_date <= locked_upto:
            #         continue
            #     if repair_floor and att.attendance_date < repair_floor:
            #         continue                
            #     buckets.add((att.employee, att.attendance_date))
            #     # if self.name == "GENERAL9_30_to_6_00":
            #     #     logger.debug(f"reconcile_late_checkins 3 attendance update employee {att.employee} date {att.attendance_date} shift {att.shift} added to buckets")

            if not buckets:
                if self.name == "Flexy":
                    logger.debug(f"reconcile_late_checkins 4 no buckets to reconcile for shift {self.name}")    
                return
            # -------------------------------------------------------
            # Build impacted employee-day buckets
            # -------------------------------------------------------
            processed_attendance = set()
            for employee, adate in sorted(buckets): 
                logger.debug(f"Reconciling attendance for employee {employee} date {adate} shift {self.name}")               
                # unlink flag
                unlinked = False  # ✅ reset per bucket
                #  Key to avoid duplicates even if multiple checkins/attendances for same employee-date
                att_key = f"{employee}::{adate}"
                if att_key in processed_attendance:
                    continue
                processed_attendance.add(att_key)                
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
                if attendance and attendance.docstatus == 2:
                    continue
                # if self.name == "GENERAL9_30_to_6_00":
                #     logger.debug(f"Processing bucket employee {employee} date {adate} attendance {attendance}")
                # ---------------------------------------------------
                # Case 1 — no attendance (holiday / weekoff)
                # ---------------------------------------------------
                if not attendance:
                    # logger.debug(
                    #     f"[repair] no attendance employee={employee} date={adate}"
                    # )
                    # unlink logs so HRMS can rebuild attendance
                    _unlink_day_checkins(employee, adate)
                    unlinked = True   # set flag
                    # find shift from checkins
                    shift_name = frappe.db.get_value(
                        "Employee Checkin",
                        {
                            "employee": employee,
                            "time": ("between", [
                                get_datetime(adate),
                                get_datetime(add_days(adate, 1))
                            ]),
                            "offshift": 0
                        },
                        "shift"
                    )
                    if shift_name:
                        shifts_to_rebuild.add(shift_name) 
                    continue
                
                # ---------------------------------------------------
                # Cancel existing attendance only for non leave-driven attendance
                # ---------------------------------------------------
                if attendance.docstatus == 1:
                    # 🚨 DO NOT delete leave-based attendance, as half day leave is followed by half day punch
                    if attendance.leave_application or attendance.leave_type:
                        # ✅ DO NOT DELETE
                        # Just unlink checkins so recalculation happens on same record
                        if not unlinked:
                            # if self.name == "GENERAL9_30_to_6_00":
                            #     logger.debug(f"reconcile_late_checkins 3-1 is leave-based; unlinking checkins for employee {employee} to trigger recalculation")
                            _unlink_day_checkins(employee, adate)
                            unlinked = True   # set flag                        
                        try:
                            # if self.name == "GENERAL9_30_to_6_00":
                            #     logger.debug(f"reconcile_late_checkins 4-1 attendance {attendance.name} is leave-based; {attendance.shift} shift; ")    
                            # ADD THIS BLOCK HERE to force attendence hooks to trigger
                            att_doc = frappe.get_doc("Attendance", attendance.name)
                                                      
                            # # FORCE CHANGE (guaranteed)
                            # att_doc.custom_recompute_trigger = random.randint(1, 10**9)                        
                            # # 🔥 DO NOT SKIP VALIDATE
                            # att_doc.flags.ignore_validate = False
                            # # allow update
                            # att_doc.save(ignore_permissions=True)
                        except Exception:
                            logger.exception(
                                f"[repair] failed to force recompute for {attendance.name}"
                            )
                        shift_name = attendance.shift
                        # 🔥 Fallback 1 — from checkin (BEST source)
                        if not shift_name:
                            shift_name = frappe.db.get_value(
                                "Employee Checkin",
                                {
                                    "employee": employee,
                                    "time": ("between", [
                                        get_datetime(add_days(adate, -1)),
                                        get_datetime(add_days(adate, 2))
                                    ]),
                                    "offshift": 0
                                },
                                "shift",
                                order_by="time asc"
                            )
                        if shift_name:
                            shift_doc = frappe.get_cached_doc("Shift Type", shift_name)
                            punch_dt = frappe.db.get_value(
                                "Employee Checkin",
                                {
                                    "employee": employee,
                                    "shift": shift_name,
                                    "time": ("between", [
                                        get_datetime(add_days(adate, -1)),
                                        get_datetime(add_days(adate, 2))
                                    ]),
                                    "offshift": 0
                                },
                                "time",
                                order_by="time asc"
                            )
                            derived_date = _attendance_date_for_punch(shift_doc, punch_dt)
                            if derived_date != adate:
                                shift_name = None
                            if shift_name:
                                # logger.debug(f"reconcile_late_checkins 4-2 attendance {attendance.name} is leave-based; adding shift {shift_name} to rebuild list for employee {employee} date {adate}")
                                shifts_to_rebuild.add(shift_name)
                    else:
                        # Normal flow (non-leave)
                        # 🔥 FIRST: find shift BEFORE deleting
                        shift_name = attendance.shift
                        if not shift_name:
                            shift_name = frappe.db.get_value(
                                "Employee Checkin",
                                {
                                    "employee": employee,
                                    "time": ("between", [
                                        get_datetime(add_days(adate, -1)),
                                        get_datetime(add_days(adate, 2))
                                    ]),
                                    "offshift": 0
                                },
                                "shift",
                                order_by="time asc"
                            )
                        if not shift_name:
                            logger.error(
                                f"[CRITICAL] No shift found for {employee} {adate}, skipping delete"
                            )
                            continue  # 🚨 NEVER delete if rebuild not possible
                        shift_doc = frappe.get_cached_doc("Shift Type", shift_name)
                        # ---------------------------------------------------
                        # FLEX SHIFT →  DELETE
                        # ---------------------------------------------------
                        if cint(getattr(shift_doc, "custom_flexible_shift", 0)):
                            logger.debug(
                                f"[FLEX DELETE] Rebuilding & deleting attendance for {attendance.name} {employee} {adate}"
                            )
                            try:
                                att_doc = frappe.get_doc("Attendance", attendance.name)
                                # cancel if submitted
                                if att_doc.docstatus == 1:
                                    att_doc.cancel()
                                frappe.delete_doc("Attendance", att_doc.name, force=1)
                            except Exception:
                                logger.exception(f"[FLEX DELETE FAILED] {attendance.name}")
                            # unlink checkins so HRMS reprocesses
                            _unlink_day_checkins(employee, adate)
                            # ensure rebuild
                            shifts_to_rebuild.add(shift_name)
                            continue
                        # ---------------------------------------------------
                        # NON-FLEX → CONTROLLED DELETE
                        # ---------------------------------------------------
                        # 🔒 Safety: avoid deleting freshly rebuilt attendance
                        att_doc = frappe.get_doc("Attendance", attendance.name)
                        if (now_datetime() - att_doc.modified).total_seconds() < 5:
                            logger.debug(
                                f"[SKIP DELETE] Recently modified {att_doc.name}, avoiding race"
                            )
                            _unlink_day_checkins(employee, adate)
                            shifts_to_rebuild.add(shift_name)
                            continue
                        # ✅ Safe delete
                        if att_doc.docstatus == 1:
                            att_doc.cancel()
                            frappe.delete_doc("Attendance", att_doc.name, force=1)
                            logger.debug(
                                f"[NON-FLEX DELETE] {attendance.name} {employee} {adate}"
                            )
                        shifts_to_rebuild.add(shift_name)
                # ---------------------------------------------------
                # Unlink checkins so HRMS recomputes
                # ---------------------------------------------------
                if not unlinked:
                    _unlink_day_checkins(employee, adate)
                # if attendance.shift:
                #     shifts_to_rebuild.add(attendance.shift)
            # -----------------------------------------------------
            # Rebuild attendance once per shift (STRICT isolation)
            # -----------------------------------------------------
            current_shift = self.name
            if self.name == "Flexy":
                logger.debug(f"Shifts to rebuild after processing buckets: {shifts_to_rebuild}")
            for shift_name in shifts_to_rebuild:
                # 🚨 HARD GUARD — prevent cross-shift rebuild
                if shift_name != current_shift:
                    continue
                # if current_shift == "GENERAL9_30_to_6_00":
                #     logger.debug(
                #         f"[repair 2] rebuilding attendance ONLY for current shift {shift_name}"
                #     )
                shift_doc = frappe.get_cached_doc("Shift Type", shift_name)
                # Optional safety flag to control hooks
                # checked in apply_nonflex_strict_hours to ensure it only runs during auto attendance, so we set it here during our repair run.
                frappe.flags.in_auto_attendance_run = True
                try:
                    shift_doc.process_auto_attendance()
                    self.run_post_attendance_rules(buckets)
                finally:
                    frappe.flags.in_auto_attendance_run = False
        finally:                     
            frappe.db.commit()
            #  release lock
            frappe.flags.vcm_repair_running = False   



    def run_post_attendance_rules(self, buckets):
        employees = list({e for e, _ in buckets})
        dates = list({d for _, d in buckets})
        attendance_list = frappe.get_all(
            "Attendance",
            filters={
                "employee": ("in", employees),
                "attendance_date": ("in", dates),
                "docstatus": 1
            },
            pluck="name"
        )
        for name in attendance_list:
            doc = frappe.get_doc("Attendance", name)
            # ✅ run ALL business rules here
            apply_nonflex_strict_hours(doc)
            

            # persist WITHOUT triggering full lifecycle
            doc.db_update()
    
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
# def _sync_leave_attendance_times(att, employee: str, adate) -> bool:
#     """
#     Update leave-driven Attendance using real punches.
#     Returns True if anything changed.
#     """
#     # ------------------------------------------------------------
#     # 1) Determine shift for this day
#     # ------------------------------------------------------------
#     shift_name = att.get("shift")
#     # If Attendance has no shift (can happen), try to infer from checkins first
#     if not shift_name:
#         inferred = frappe.get_value(
#             "Employee Checkin",
#             {"employee": employee, "skip_auto_attendance": 0, "offshift": 0, "time": ("between", [adate, add_days(adate, 1)])},
#             "shift",
#         )
#         if inferred:
#             shift_name = inferred
#     # If still missing, we cannot compute window safely
#     if not shift_name:
#         # logger.debug(f"[LEAVE-SYNC] No shift on Attendance and could not infer shift from checkins for {employee} {adate}")
#         return False
#     shift = frappe.get_cached_doc("Shift Type", shift_name)

#      # ------------------------------------------------------------
#     # 2) Compute valid scan window (overnight + grace)
#     # ------------------------------------------------------------
#     win_start, win_end = _shift_scan_window(shift, adate)
#     # logger.debug(f"Computed scan window for leave-driven attendance sync for employee={employee} date={adate} shift={shift.name}: win_start={win_start} win_end={win_end}") 
#     if not win_start or not win_end:
#         return False
#     # logger.debug(f"Syncing leave attendance for employee={employee} date={adate} using shift {shift.name} with scan window {win_start} to {win_end}")   
#     # ------------------------------------------------------------
#     # 3) Fetch valid checkins within window
#     # ------------------------------------------------------------
#     logs = frappe.get_all(
#         "Employee Checkin",
#         filters={
#             "employee": employee,
#             "skip_auto_attendance": 0,
#             "offshift": 0,
#             "time": ("between", [win_start, win_end]),
#         },
#         fields=["name", "time", "shift"],
#         order_by="time asc",
#         limit_page_length=5000,
#     )
#     if not logs:
#         # logger.debug(f"[LEAVE-SYNC] No valid checkins in window for {employee} {adate}")
#         return False
#     # Prefer logs mapped to same shift (keeps you close to HRMS)
#     same_shift = [l for l in logs if l.get("shift") == shift.name]
#     if same_shift:
#         logs = same_shift
#     # ------------------------------------------------------------
#     # 4) Compute day in/out and working hours from those logs
#     # ------------------------------------------------------------
#     in_dt = min(get_datetime(x.time) for x in logs)
#     out_dt = max(get_datetime(x.time) for x in logs)
#     seconds = max(0, (out_dt - in_dt).total_seconds())
#     wh = flt(seconds / 3600.0, 2)
#     old_in = att.get("in_time")
#     old_out = att.get("out_time")
#     old_wh = att.get("working_hours")
#     # logger.debug(
#     #     f"[LEAVE-SYNC] {employee} {adate}: old_in={old_in} old_out={old_out} old_wh={old_wh} "
#     #     f"new_in={in_dt} new_out={out_dt} new_wh={wh}"
#     # )
#     # ------------------------------------------------------------
#     # 5) Update Attendance without changing leave status, we can not use save as doc is already submitted
#     # ------------------------------------------------------------
#     frappe.db.set_value(
#         "Attendance",
#         att["name"],
#         {
#             "in_time": in_dt,
#             "out_time": out_dt,
#             "working_hours": wh,
#             "shift": shift.name,
#         },
#     )
#     #  now update all flags
#     att_doc = frappe.get_doc("Attendance", att["name"])
#     # update memory object
#     att_doc.in_time = in_dt
#     att_doc.out_time = out_dt
#     att_doc.working_hours = wh
#     att_doc.shift = shift.name
#     # run the same policy logic that validate() normally runs
#     from vcmhrms.vcm_hrms.overrides.attendance_hooks import (
#         apply_nonflex_strict_hours,
#         enforce_late_entry_policy,
#     )
#     # logger.debug(f"Applying attendance policies for leave-driven attendance sync for {employee} {adate}")   
#     apply_nonflex_strict_hours(att_doc)
#     enforce_late_entry_policy(att_doc)
#     frappe.db.set_value(
#         "Attendance",
#         att_doc.name,
#         {
#             "in_time": in_dt,
#             "out_time": out_dt,
#             "working_hours": wh,
#             "shift": shift.name,
#             "late_entry": att_doc.late_entry,
#             "early_exit": att_doc.early_exit,
#             "custom_late_entry_within_grace": att_doc.custom_late_entry_within_grace,
#             "custom_first_half_done": att_doc.custom_first_half_done,
#             "custom_second_half_done": att_doc.custom_second_half_done,
#             "custom_sandwich_applied": att_doc.custom_sandwich_applied,
#             "custom_created_by_sandwich": att_doc.custom_created_by_sandwich,
#         },
#     )
#     # ------------------------------------------------------------
#     # 6) Link only these logs to the existing leave Attendance
#     # ------------------------------------------------------------
#     names = [l["name"] for l in logs]
#     frappe.db.sql(
#         f"""
#         UPDATE `tabEmployee Checkin`
#         SET attendance = %s
#         WHERE name IN ({",".join(["%s"] * len(names))})
#         """,
#         tuple([att["name"]] + names),
#     )
#     return True

@frappe.whitelist()
def run_auto_attendance_now(employee: str | None = None):
    """
    Manual trigger for testers.
    Fixes:
    - Shared repair_start across all shifts
    - last_repair_run_at updated ONLY once after all shifts
    - Prevents shift-wise data loss due to watermark overwrite
    """
    repair_start = now_datetime()   # ✅ single snapshot for entire run
    details = {"started_at": str(repair_start)}
    processed = 0
    errors = 0
    try:
        if employee:
            logger.debug(f"Running auto attendance for employee {employee} with repair_start {repair_start}")
            # ---------------------------------------
            # Employee-specific mode
            # ---------------------------------------
            shift_names = frappe.get_all(
                "Shift Assignment",
                filters={"employee": employee, "docstatus": 1, "status": "Active"},
                pluck="shift_type",
            )
            for s in set(shift_names):
                try:
                    doc = frappe.get_cached_doc("Shift Type", s)

                    # 🔥 IMPORTANT: pass shared repair_start
                    doc.process_auto_attendance(is_manually_triggered=True)
                    # OR if process_auto_attendance internally calls it:
                    # doc.process_auto_attendance(is_manually_triggered=True, repair_start=repair_start)
                    processed += 1
                except Exception:
                    errors += 1
                    frappe.log_error(
                        title=f"Auto attendance failed shift={s}",
                        message=frappe.get_traceback(),
                    )
            details.update({
                "mode": "employee",
                "employee": employee,
                "shifts": list(set(shift_names)),
            })
        else:
            # ---------------------------------------
            # All shifts mode (CUSTOM, NOT HRMS default)
            # ---------------------------------------
            shifts = frappe.get_all(
                "Shift Type",
                filters={"enable_auto_attendance": 1},
                pluck="name",
            )
            # logger.debug(f"Running auto attendance for all shifts {shifts} with repair_start {repair_start}")
            for s in shifts:
                try:
                    doc = frappe.get_cached_doc("Shift Type", s)
                    # 🔥 IMPORTANT: pass same repair_start to all shifts
                    # doc.reconcile_late_checkins(repair_start=repair_start)
                    doc.process_auto_attendance(is_manually_triggered=True)
                    processed += 1
                except Exception:
                    errors += 1
                    frappe.log_error(
                        title=f"Auto attendance failed shift={s}",
                        message=frappe.get_traceback(),
                    )
            details.update({
                "mode": "all_shifts",
                "total_shifts": len(shifts),
            })
        # ---------------------------------------
        # ✅ UPDATE WATERMARK ONCE (CRITICAL FIX)
        # ---------------------------------------
        # logger.debug(f"Updating last_repair_run_at to {repair_start} after processing {processed} shifts with {errors} errors")
        frappe.db.set_value(
            "VCM HR Settings",
            "VCM HR Settings",
            "last_repair_run_at",
            repair_start,
        )
        frappe.db.commit()
        return {
            "ok": errors == 0,
            "processed": processed,
            "errors": errors,
            "details": details,
        }
    finally:
        # safety unlock (if you use global flag)
        frappe.flags.vcm_repair_running = False

def date_range(start_date, end_date):
        start = getdate(start_date)
        end = getdate(end_date)
        days = []
        d = start
        while d <= end:
            days.append(d)
            d = add_days(d, 1)
        return days