# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import traceback

import frappe
from frappe.utils import getdate, nowdate, add_days, now_datetime, get_datetime, cint, flt, today
import logging
import random
from datetime import datetime, timedelta, time as pytime
from hrms.hr.doctype.shift_type.shift_type import ShiftType as HRMSShiftType
from vcmhrms.vcm_hrms.overrides.attendance_hooks import (
    apply_flex_min_hours, 
    apply_nonflex_strict_hours, 
    _strict_shift_bounds_and_lunch, 
    _payroll_period_for_date, 
    CHECKIN_ON_TIME_GRACE_MINUTES,
)
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
                    "log_type": ["in", ["IN", "OUT"]]
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
        logger.debug("in process_auto_attendance (forcing safe non-manual mode)")
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
            # Fetch late or modified checkins (STRICT SHIFT FILTER)
            # ------------------------------------------------------- 
            checkins = frappe.get_all(
                "Employee Checkin",
                filters={
                    "modified": (">", from_dt),
                    "time": (">=", repair_floor),
                    "offshift": 0,
                    "shift": self.name,
                    "log_type": ["in", ["IN", "OUT"]]  # safest explicit filter
                },
                fields=["employee", "time", "shift", "log_type"],
                order_by="employee, time asc",
                limit_page_length=5000,
            )
            # if self.name == "Flexy":
            #     logger.debug(f"reconcile_late_checkins checkins {(checkins)} , to reconcile for shift {self.name}")    
            
            # -------------------------------------------------------
            # BUILD BUCKETS (STRICT: employee, date, shift)
            # -------------------------------------------------------
            buckets = set()
            for c in checkins:
                punch_dt = get_datetime(c.time)
                if not punch_dt:
                    continue
                shift_doc = frappe.get_cached_doc("Shift Type", c.shift)
                adate = _attendance_date_for_punch(shift_doc, punch_dt)
                
                if locked_upto and adate <= locked_upto:
                    continue
                if repair_floor and adate < repair_floor:
                    continue
                buckets.add((c.employee, adate, c.shift))
            # -------------------------------------------------------
            # Check Leave Update as we need to process them also  MAP TO SHIFT 
            # -------------------------------------------------------
            leave_updates = frappe.get_all(
                "Leave Application",
                filters={
                    "modified": (">", from_dt),
                    "docstatus": ("<", 2)
                },
                fields=["employee", "from_date", "to_date", "half_day", "half_day_date"]
            )
            # if self.name == "Flexy":
            #     logger.debug(f" reconcile_late_checkins 1-1 leave_updates {leave_updates} {from_dt} shift {self.name}") 
            for l in leave_updates:
                dates = [l.half_day_date] if l.half_day and l.half_day_date else date_range(l.from_date, l.to_date) 
                for d in dates:
                    if locked_upto and d <= locked_upto:
                        continue
                    if repair_floor and d < repair_floor:
                        continue
                    # 🔥 resolve via attendance ONLY
                    attendance = frappe.get_value(
                        "Attendance",
                        {
                            "employee": l.employee,
                            "attendance_date": d,
                            "docstatus": ("<", 2)
                        },
                        ["shift"],
                        as_dict=True
                    )
                    if not attendance or attendance.shift != self.name:
                        continue  
                    buckets.add((l.employee, d, self.name))                 
            if not buckets:
                # logger.debug(f"no bucket returning")
                return
            # logger.debug(f"reco_late_checkin Processing {len(buckets)} impacted employee-day-shift buckets for shift {self.name}: {buckets}")
            # =========================================================
            # 4. PROCESS BUCKETS
            # =========================================================
            processed = set()            
            for employee, adate, shift_name in sorted(buckets):
                # HARD GUARD
                if shift_name != self.name:
                    continue
                key = f"{employee}::{adate}::{shift_name}"
                if key in processed:
                    continue
                processed.add(key)
                # logger.debug(f"[PROCESS 1] {employee} {adate} shift={shift_name}")
                # -----------------------------------------
                # STEP 1 — STRICT (your original logic)
                # -----------------------------------------
                attendance = frappe.get_value(
                    "Attendance",
                    {
                        "employee": employee,
                        "attendance_date": adate,
                        "shift": shift_name,  # 🔥 CRITICAL FIX
                        "docstatus": ("<", 2),
                    },
                    [
                        "name",
                        "docstatus",
                        "shift",
                        "leave_application",
                        "leave_type",
                    ],
                    as_dict=True,
                )
                # logger.debug(f"[PROCESS 2] {employee} {adate} shift={shift_name}, att {attendance}")
                # -----------------------------------------
                # STEP 2 — FALLBACK (ONLY if strict fails)
                # -----------------------------------------
                if not attendance:
                    attendance = frappe.get_value(
                        "Attendance",
                        {
                            "employee": employee,
                            "attendance_date": adate,
                            "docstatus": ("<", 2),
                        },
                        [
                            "name",
                            "docstatus",
                            "shift",
                            "leave_application",
                            "leave_type",
                        ],
                        as_dict=True,
                    )                    
                # logger.debug(f"[PROCESS 3] {employee} {adate} shift={shift_name} , att {attendance}")
                if self.name == "GENERAL9_30_to_6_00":
                    logger.debug(f"Processing bucket employee {employee} date {adate} attendance {attendance}")
                # ---------------------------------------------------
                # Case 1 — no attendance (holiday / weekoff)
                # ---------------------------------------------------
                if not attendance:
                    # logger.debug(
                    #     f"[repair] no attendance employee={employee} date={adate}"
                    # )
                    # unlink logs so HRMS can rebuild attendance
                    _unlink_day_checkins(employee, adate)                    
                    shifts_to_rebuild.add(shift_name) 
                    continue
                if attendance.docstatus == 2:
                    continue
                            
                # =====================================================
                # CASE 2 — LEAVE ATTENDANCE → RECOMPUTE (NO DELETE)
                # =====================================================
                att_doc = frappe.get_doc("Attendance", attendance.name) 
                # in sandwich leave shift is not coming to attendence
                if not att_doc.shift:
                    att_doc.shift = shift_name  
                if attendance.leave_application or attendance.leave_type:
                    # logger.debug(f"[LEAVE RECOMPUTE] {att_doc.name}")
                    try:                        
                        # 🔥 STEP 1: recompute IN/OUT from checkins
                        shift_doc = frappe.get_cached_doc("Shift Type", shift_name)
                        start, end = _shift_scan_window(shift_doc, adate)
                        checkins = frappe.get_all(
                            "Employee Checkin",
                            filters={
                                "employee": employee,
                                "shift": shift_name,
                                "time": ("between", [start, end]),
                                "offshift": 0,
                                "log_type": ["in", ["IN", "OUT"]]
                            },
                            fields=["time"],
                            order_by="time asc"
                        )
                        # 🔥 USE ATTENDANCE STATUS (CORRECT SOURCE)
                        status = (att_doc.status or "").strip()
                        # -------------------------------------------------
                        # 🔥 CASE A: LEAVE + NO PUNCH We have to consider full day leave or half day leave
                        # -------------------------------------------------
                        # -------------------------------------------------
                        # FULL DAY LEAVE (🔥 FIX)
                        # -------------------------------------------------
                        if status == "On Leave":
                            # logger.debug(f"[LEAVE FULL] {attendance.name} with {len(checkins)} punches")
                            if checkins:
                                # OT case
                                att_doc.in_time = checkins[0].time
                                att_doc.out_time = checkins[-1].time
                                # logger.debug(f"[LEAVE FULL 1] att_doc.in_time {att_doc.in_time} att_doc.out_time {att_doc.out_time} ")
                                shift_doc = frappe.get_cached_doc("Shift Type", shift_name)
                                if cint(getattr(shift_doc, "custom_flexible_shift", 0)):
                                    apply_flex_min_hours(att_doc)
                                else:
                                    apply_nonflex_strict_hours(att_doc)
                                #  link checkin to attendence 
                                _link_day_checkins(
                                    employee,
                                    shift_name,
                                    start,
                                    end,
                                    att_doc.name
                                )
                                # logger.debug(f"[LEAVE FULL 2] docname {att_doc.name} , att_doc.in_time {att_doc.in_time} att_doc.out_time {att_doc.out_time} hr {att_doc.working_hours}")
                                frappe.db.set_value(
                                    "Attendance",
                                    att_doc.name,
                                    {
                                        "in_time": att_doc.in_time,
                                        "out_time": att_doc.out_time,
                                        "working_hours": att_doc.working_hours,
                                        "status": "On Leave",  # 🔥 FORCE BACK
                                        "shift": att_doc.shift,
                                        "half_day_status": None,
                                        "half_day_status": att_doc.half_day_status,
                                        "custom_late_entry_within_grace": att_doc.custom_late_entry_within_grace,
                                        "custom_first_half_done": att_doc.custom_first_half_done,
                                        "custom_second_half_done":att_doc.custom_second_half_done,                                        
                                        "modified": now_datetime(),
                                    }
                                )
                            else:
                                # logger.debug(f"[LEAVE FULL 3] NO checkin ")
                                frappe.db.set_value(
                                    "Attendance",
                                    att_doc.name,
                                    {
                                        "status": "On Leave",
                                        "half_day_status": None,
                                        "working_hours": 0,
                                        "in_time": None,
                                        "out_time": None,
                                        "half_day_status": att_doc.half_day_status,
                                        "custom_late_entry_within_grace": att_doc.custom_late_entry_within_grace,
                                        "custom_first_half_done": att_doc.custom_first_half_done,
                                        "custom_second_half_done":att_doc.custom_second_half_done,
                                        "modified": now_datetime(),
                                    }
                                )
                        # -------------------------------------------------
                        # HALF DAY LEAVE
                        # -------------------------------------------------
                        elif status == "Half Day":                            
                            if not checkins:  
                                # logger.debug(f"[LEAVE HALF NO PUNCH] {attendance.name}, other half {att_doc.half_day_status}")                              
                                frappe.db.set_value(
                                    "Attendance",
                                    attendance.name,
                                    {
                                        "status": "Half Day",
                                        "half_day_status": "Absent",
                                        "working_hours": 0,
                                        "in_time": None,
                                        "out_time": None,
                                        "half_day_status": "Absent",
                                        "custom_late_entry_within_grace": att_doc.custom_late_entry_within_grace,
                                        "custom_first_half_done": att_doc.custom_first_half_done,
                                        "custom_second_half_done":att_doc.custom_second_half_done,
                                        "modified": now_datetime(),
                                    }
                                )
                            else:                                
                                # logger.debug(f"[LEAVE HALF WITH PUNCH] {attendance.name}")
                                att_doc.in_time = checkins[0].time
                                att_doc.out_time = checkins[-1].time
                                shift_doc = frappe.get_cached_doc("Shift Type", shift_name)
                                if cint(getattr(shift_doc, "custom_flexible_shift", 0)):
                                    apply_flex_min_hours(att_doc)
                                else:
                                    apply_nonflex_strict_hours(att_doc)
                                #  link checkin to attendence 
                                _link_day_checkins(
                                    employee,
                                    shift_name,
                                    start,
                                    end,
                                    att_doc.name
                                )
                                frappe.db.set_value(
                                    "Attendance",
                                    att_doc.name,
                                    {
                                        "in_time": att_doc.in_time,
                                        "out_time": att_doc.out_time,
                                        "working_hours": att_doc.working_hours,
                                        "status": "Half Day",  # FORCE
                                        "half_day_status": att_doc.half_day_status,
                                        "custom_late_entry_within_grace": att_doc.custom_late_entry_within_grace,
                                        "custom_first_half_done": att_doc.custom_first_half_done,
                                        "custom_second_half_done":att_doc.custom_second_half_done,
                                        "modified": now_datetime(),
                                    }
                                ) 
                        else:
                            # fallback (should not happen)
                            logger.debug(f"[LEAVE UNKNOWN STATUS] {att_doc.name} status={status}")                               
                        # logger.debug(f"[LEAVE RECOMPUTE SUCCESS] {att_doc.name}")
                    except Exception:
                        logger.exception(f"[LEAVE RECOMPUTE FAILED] {att_doc.name}")
                    continue
                # =====================================================
                # CASE 3 — FLEX SHIFT → CONTROLLED DELETE
                # =====================================================
                shift_doc = frappe.get_cached_doc("Shift Type", shift_name)
                if cint(getattr(shift_doc, "custom_flexible_shift", 0)):
                    # 🔒 prevent delete loop (CRITICAL)
                    if (now_datetime() - att_doc.modified).total_seconds() < 5:
                        # logger.debug(f"[FLEX SKIP DELETE] Fresh {att_doc.name}")
                        _unlink_day_checkins(employee, adate)
                        shifts_to_rebuild.add(shift_name)
                        continue
                    # logger.debug(f"[FLEX DELETE] {att_doc.name}")
                    try:
                        if att_doc.docstatus == 1:
                            att_doc.cancel()
                        # logger.debug(f"[FLEX SKIP DELETE] Fresh {att_doc.name}")
                        frappe.delete_doc("Attendance", att_doc.name, force=1)
                    except Exception:
                        logger.exception(f"[FLEX DELETE FAILED] {att_doc.name}")
                    _unlink_day_checkins(employee, adate)
                    shifts_to_rebuild.add(shift_name)
                    continue
                # =====================================================
                # CASE 4 — NON FLEX (CONTROLLED DELETE)
                # =====================================================
                if (now_datetime() - att_doc.modified).total_seconds() < 5:
                    # logger.debug(f"[SKIP DELETE] Fresh {att_doc.name}")
                    _unlink_day_checkins(employee, adate)
                    shifts_to_rebuild.add(shift_name)
                    continue
                if att_doc.docstatus == 1:
                    att_doc.cancel()
                    frappe.delete_doc("Attendance", att_doc.name, force=1)
                # logger.debug(f"[NON FLEX DELETE] {att_doc.name}")
                _unlink_day_checkins(employee, adate)
                shifts_to_rebuild.add(shift_name)
            # =========================================================
            # 5. REBUILD (STRICT SHIFT ISOLATION)
            # =========================================================
            
            for shift_name in shifts_to_rebuild:
                if shift_name != self.name:
                    continue
                # logger.debug(f"[REBUILD] shift={shift_name}")
                shift_doc = frappe.get_cached_doc("Shift Type", shift_name)
                frappe.flags.in_auto_attendance_run = True
                try:
                    # logger.debug("BEFORE PROCESS AUTO")
                    shift_doc.process_auto_attendance()  
                    # logger.debug("AFTE PROCESS AUTO")                   
                finally:
                    frappe.flags.in_auto_attendance_run = False            
        finally:
            frappe.db.commit()
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


@frappe.whitelist()
def run_late_grace_quota_now():
    if frappe.cache().get_value("late_grace_running"):
        return {"already_running": True}
    frappe.cache().set_value("late_grace_running", True)
    frappe.enqueue(
        "vcmhrms.vcm_hrms.overrides.VCMShiftType.run_late_grace_quota_background",
        queue="long",
        timeout=4000,
        job_name="late_grace_quota_run",
        enqueue_after_commit=True
    )
    return {"queued": True}

import traceback

@frappe.whitelist()
def run_late_grace_quota_background():
    frappe.set_user("Administrator")
    processed = 0
    updated = 0
    frappe.cache().set_value("late_grace_status", {
        "status": "running",
        "processed": 0,
        "updated": 0
    })
    try:
        settings = frappe.get_cached_doc("VCM HR Settings")
        lock_date = settings.attendance_locked_upto
        if not lock_date:
            raise Exception("Attendance lock date is required")
        lock_date = getdate(lock_date)
        from_date = add_days(lock_date, 1)
        to_date = getdate(nowdate())
        allowed = cint(settings.allowed_late_entry_count or 0)
        # -----------------------------------
        # STEP 1: employees
        # -----------------------------------
        employees = frappe.get_all(
            "Attendance",
            filters={
                "attendance_date": ["between", [from_date, to_date]],
                "docstatus": ["!=", 2]
            },
            distinct=True,
            pluck="employee"
        )
        total = len(employees)
        # -----------------------------------
        # STEP 2: per employee
        # -----------------------------------
        for idx, employee in enumerate(employees, start=1):
            try:
                records = frappe.get_all(
                    "Attendance",
                    filters={
                        "employee": employee,
                        "attendance_date": ["between", [from_date, to_date]],
                        "docstatus": ["!=", 2]
                    },
                    fields=[
                        "name",
                        "attendance_date",
                        "in_time",
                        "out_time",
                        "shift",
                        "custom_late_entry_within_grace"
                    ],
                    order_by="attendance_date asc"
                )
                if not records:
                    continue
                eligible = []
                # -----------------------------------
                # STEP 3: eligibility
                # -----------------------------------
                for r in records:
                    if not r.in_time or not r.shift:
                        continue
                    shift = frappe.get_cached_doc("Shift Type", r.shift)
                    if not cint(shift.enable_late_entry_marking):
                        continue
                    late_grace = cint(shift.late_entry_grace_period or 0)
                    in_dt = get_datetime(r.in_time)
                    strict_start, strict_end, _, _ = _strict_shift_bounds_and_lunch(
                        shift=shift,
                        attendance_date=r.attendance_date,
                        in_dt=in_dt,
                        out_dt=get_datetime(r.out_time) if r.out_time else in_dt
                    )
                    if not strict_start:
                        continue
                    on_time_grace_minutes = CHECKIN_ON_TIME_GRACE_MINUTES
                    policy_late_start = strict_start + timedelta(minutes=on_time_grace_minutes)

                    grace_cutoff = strict_start + timedelta(minutes=late_grace)

                    if in_dt >= policy_late_start and in_dt <= grace_cutoff:
                        eligible.append({
                            "name": r.name,
                            "attendance_date": r.attendance_date
                        })
                if not eligible:
                    continue
                # -----------------------------------
                # STEP 4: sort
                # -----------------------------------
                eligible.sort(key=lambda x: (x["attendance_date"], x["name"]))
                # -----------------------------------
                # STEP 5: apply quota
                # -----------------------------------
                allowed_names = {row["name"] for row in eligible[:allowed]}
                for r in records:
                    should_be_flag = 1 if r.name in allowed_names else 0
                    current_flag = cint(r.custom_late_entry_within_grace or 0)
                    if current_flag == should_be_flag:
                        continue
                    doc = frappe.get_doc("Attendance", r.name)
                    doc.custom_late_entry_within_grace = should_be_flag
                    apply_nonflex_strict_hours(doc)
                    frappe.db.set_value(
                        "Attendance",
                        doc.name,
                        {
                            "custom_late_entry_within_grace": doc.custom_late_entry_within_grace,
                            "status": doc.status,
                            "working_hours": doc.working_hours,
                            "late_entry": doc.late_entry,
                            "custom_first_half_done": doc.custom_first_half_done,
                            "custom_second_half_done": doc.custom_second_half_done,
                            "half_day_status": doc.half_day_status,
                            "modified": now_datetime(),
                        }
                    )
                    updated += 1
                processed += 1
                # ✅ update live status
                frappe.cache().set_value("late_grace_status", {
                    "status": "running",
                    "processed": processed,
                    "updated": updated,
                    "total": total,
                    "current_employee": employee
                })
                # ✅ commit per employee (VERY IMPORTANT)
                frappe.db.commit()
            except Exception:
                tb = traceback.format_exc()
                frappe.log_error(tb, f"Late Grace Failed Employee: {employee}")
                frappe.cache().set_value("late_grace_status", {
                    "status": "failed",
                    "error": f"{employee}:\n{tb[:1000]}"
                })
                frappe.db.rollback()
                continue  # fail fast (debug friendly)
        # -----------------------------------
        # FINAL STATUS
        # -----------------------------------
        frappe.cache().set_value("late_grace_status", {
            "status": "completed",
            "processed": processed,
            "updated": updated,
            "total": total
        })
    except Exception:
        tb = traceback.format_exc()
        frappe.log_error(tb, "Late Grace Global Fatal Error")
        frappe.cache().set_value("late_grace_status", {
            "status": "failed",
            "error": tb[:1500]
        })
    finally:
        frappe.cache().delete_value("late_grace_running")
        # logger.debug("[LATE GRACE GLOBAL] COMPLETE")

@frappe.whitelist()
def get_late_grace_status():
    return frappe.cache().get_value("late_grace_status") or {}

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

def _link_day_checkins(employee, shift_name, start, end, attendance_name):
    """
    Link all valid checkins in a shift window to the given Attendance.
    """
    checkins = frappe.get_all(
        "Employee Checkin",
        filters={
            "employee": employee,
            "shift": shift_name,
            "time": ("between", [start, end]),
            "offshift": 0,
        },
        fields=["name"],
        order_by="time asc",
    )
    for c in checkins:
        frappe.db.set_value(
            "Employee Checkin",
            c.name,
            "attendance",
            attendance_name,
            update_modified=False,
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


CACHE_KEY_RUNNING = "auto_attendance_running"
CACHE_KEY_STATUS = "auto_attendance_status"

@frappe.whitelist()
def run_auto_attendance_now(employee: str | None = None):
    """
    Entry point from frontend.
    Prevents duplicate runs and enqueues background job.
    """
    # logger.debug(f"in run_auto_attendance_now {employee} ")  
    if frappe.cache().get_value(CACHE_KEY_RUNNING):
        return {"already_running": True}
    frappe.cache().set_value(CACHE_KEY_RUNNING, True)
    frappe.enqueue(
        "vcmhrms.vcm_hrms.overrides.VCMShiftType.run_auto_attendance_background",
        queue="long",
        timeout=6000,
        job_name="auto_attendance_run",
        employee=employee,
        enqueue_after_commit=True
    )
    # logger.debug(f"exit run_auto_attendance_now  ")  
    return {"queued": True}

@frappe.whitelist()
#  sudo supervisorctl tail -f frappe-bench-workers:frappe-bench-frappe-long-worker-0
def run_auto_attendance_background(employee: str | None = None):
    """
    Background execution:
    - Single repair_start for entire run
    - Per-shift processing
    - Safe commit/rollback
    - Live status tracking
    """
    frappe.set_user("Administrator")  
    repair_start = now_datetime()
    processed = 0
    errors = 0
    frappe.cache().set_value(CACHE_KEY_STATUS, {
        "status": "running",
        "processed": 0,
        "errors": 0,
        "started_at": str(repair_start)
    })
    logger.debug(f"in run_auto_attendance_background  ")  
    try:
        # ---------------------------------------
        # SHIFT FETCH
        # ---------------------------------------
        if employee:
            shift_names = frappe.get_all(
                "Shift Assignment",
                filters={
                    "employee": employee,
                    "docstatus": 1,
                    "status": "Active"
                },
                pluck="shift_type"
            )
            shifts = list(set(shift_names))
        else:
            shifts = frappe.get_all(
                "Shift Type",
                filters={"enable_auto_attendance": 1},
                pluck="name"
            )
        total = len(shifts)
        # logger.debug(f"in run_auto_attendance_background total shift to process {total} ") 
        # ---------------------------------------
        # MAIN LOOP
        # ---------------------------------------
        for idx, shift_name in enumerate(shifts, start=1):
            try:
                doc = frappe.get_cached_doc("Shift Type", shift_name)                
                doc.process_auto_attendance(is_manually_triggered=True)
                processed += 1
                # ✅ update progress
                frappe.cache().set_value(CACHE_KEY_STATUS, {
                    "status": "running",
                    "processed": processed,
                    "errors": errors,
                    "total": total,
                    "current_shift": shift_name,
                    "progress": round((processed / total) * 100, 1)
                })
                frappe.db.commit()
            except Exception:
                errors += 1
                tb = traceback.format_exc()
                frappe.log_error(tb, f"Shift failed: {shift_name}")
                # 🔥 CRITICAL: expose first error to UI
                frappe.cache().set_value(CACHE_KEY_STATUS, {
                    "status": "failed",
                    "error": f"{shift_name}:\n{tb[:1500]}"
                })
                frappe.db.rollback()
                return  # stop early for debugging
        # ---------------------------------------
        # UPDATE WATERMARK (ONCE ONLY)
        # ---------------------------------------
        frappe.db.set_value(
            "VCM HR Settings",
            "VCM HR Settings",
            "last_repair_run_at",
            repair_start,
        )
        frappe.db.commit()
        # ---------------------------------------
        # FINAL STATUS
        # ---------------------------------------
        frappe.cache().set_value(CACHE_KEY_STATUS, {
            "status": "completed" if errors == 0 else "failed",
            "processed": processed,
            "errors": errors,
            "total": total,
            "finished_at": str(now_datetime())
        })
        logger.debug(f"in run_auto_attendance_background processed {processed} errors {errors}, total {total} ") 
    except Exception:
        tb = traceback.format_exc()

        frappe.log_error(tb, "Auto Attendance Background Fatal Error")

        frappe.cache().set_value(CACHE_KEY_STATUS, {
            "status": "failed",
            "error": tb[:5000]  # truncate to avoid Redis overflow
        })
    finally:
        frappe.cache().delete_value(CACHE_KEY_RUNNING)

@frappe.whitelist()
def get_auto_attendance_status():
    return frappe.cache().get_value(CACHE_KEY_STATUS) or {}

def date_range(start_date, end_date):
        start = getdate(start_date)
        end = getdate(end_date)
        days = []
        d = start
        while d <= end:
            days.append(d)
            d = add_days(d, 1)
        return days