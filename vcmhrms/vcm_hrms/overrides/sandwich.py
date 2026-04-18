# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt
import traceback

import frappe
from frappe.utils import getdate, nowdate, add_days, get_datetime, cint, flt, now_datetime, date_diff
from vcmhrms.vcm_hrms.overrides.VCMShiftType import _unlink_day_checkins

import logging
logger = logging.getLogger(__name__)
SANDWICH_LEAVE_TYPE = "Sandwich Absent"

# ------------------------------------------------------------
# Night scheduler entry
# ------------------------------------------------------------
@frappe.whitelist()
def run_sandwich_now():
    if frappe.cache().get_value("sandwich_running"):
        return {"already_running": True}
    frappe.cache().set_value("sandwich_running", True)
    frappe.enqueue(
        "vcmhrms.vcm_hrms.overrides.sandwich.run_sandwich_background",
        queue="long",
        timeout=4000,
        job_name="sandwich_run",
        enqueue_after_commit=True
    )
    return {"queued": True}

def run_sandwich_background(employee=None):
    frappe.set_user("Administrator")
    frappe.cache().set_value("sandwich_status", {
        "status": "running",
        "processed": 0,
        "created": 0,
        "cleared": 0
    })
    try:
        settings = frappe.get_cached_doc("VCM HR Settings")
        locked_upto = getdate(settings.attendance_locked_upto) if settings.attendance_locked_upto else None
        today = getdate(nowdate())
        # ----------------------------
        # DATE WINDOW
        # ----------------------------
        start = add_days(today, -5)
        if locked_upto:
            start = add_days(locked_upto, 1)
        if locked_upto and start <= locked_upto:
            start = add_days(locked_upto, 1)
        # ----------------------------
        # FULLY LOCKED
        # ----------------------------
        if start > today:
            frappe.cache().set_value("sandwich_status", {
                "status": "completed",
                "processed": 0,
                "created": 0,
                "cleared": 0,
                "skipped": "range fully locked"
            })
            return
        # ---------------------------------------
        # CALL CORE LOGIC (MODIFIED BELOW)
        # ---------------------------------------
        result = run_sandwich_policy_background(start, today, employee)

        # ----------------------------
        # DEFENSIVE CHECK (CRITICAL FIX)
        # ----------------------------
        if not result:
            frappe.cache().set_value("sandwich_status", {
                "status": "failed",
                "error": "Sandwich returned no result"
            })
            return

        if not result.get("ok", True):
            frappe.cache().set_value("sandwich_status", {
                "status": "failed",
                "error": result.get("error", "Unknown error")
            })
            return
        # ----------------------------
        # SUCCESS
        # ----------------------------
        frappe.cache().set_value("sandwich_status", {
            "status": "completed",
            "processed": result.get("processed", 0),
            "created": result.get("created", 0),
            "cleared": result.get("cleared", 0),
            "scanned": result.get("scanned"),
            "total": result.get("total")
        })
    except Exception:
        tb = traceback.format_exc()
        frappe.log_error(tb, "Sandwich Background Failed")
        frappe.cache().set_value("sandwich_status", {
            "status": "failed",
            "error": tb[:1500]
        })
    finally:
        frappe.cache().delete_value("sandwich_running")

# ------------------------------------------------------------
# Main  runner where we do everything in one go for an employee 
# ------------------------------------------------------------
@frappe.whitelist()
def run_sandwich_policy_background(from_date=None, to_date=None, employee=None):
    """
    Optimized Sandwich Rule
    """
    to_date = getdate(to_date or nowdate())
    from_date = getdate(from_date)
    # -----------------------------------
    # LOCK HANDLING
    # -----------------------------------
    settings = frappe.get_cached_doc("VCM HR Settings")
    locked_upto = getdate(settings.attendance_locked_upto) if getattr(settings, "attendance_locked_upto", None) else None
    if locked_upto and from_date <= locked_upto:
        from_date = add_days(locked_upto, 1)
    if locked_upto and to_date <= locked_upto:
        return {"ok": True, "skipped": "range fully locked"}
    if from_date > to_date:
        return {"ok": True, "skipped": "invalid date range"}
    # -----------------------------------
    # EMPLOYEE LIST
    # -----------------------------------
    employees = [employee] if employee else frappe.get_all(
        "Employee",
        filters={"status": "Active"},
        pluck="name"
    )
    total = len(employees)
    created = 0
    cleared = 0
    processed = 0
    scanned = 0  # 👈 real progress
    UPDATE_EVERY = 10  # reduce Redis load
    # -----------------------------------
    # MAIN LOOP
    # -----------------------------------
    for idx, emp in enumerate(employees, start=1):
        scanned += 1
        try:
            # Skip if no holidays
            hol_dates = _get_holiday_dates_for_employee(emp, from_date, to_date)
            if not hol_dates:
                continue
            # Skip if no shift
            if not _has_shift_in_range(emp, from_date, to_date):
                continue
            res = _run_sandwich_for_employee(emp, from_date, to_date, hol_dates)
            created += res["created"]
            cleared += res["cleared"]
            processed += 1
        except Exception:
            tb = frappe.get_traceback()
            frappe.log_error(tb, f"Sandwich failed for {emp}")
            frappe.cache().set_value("sandwich_status", {
                "status": "failed",
                "error": f"{emp}\n{tb[:1000]}"
            })
            frappe.db.rollback()
            return {
                "ok": False,
                "processed": processed,
                "created": created,
                "cleared": cleared,
                "error": tb
            }
        # -----------------------------------
        # BATCHED STATUS UPDATE
        # -----------------------------------
        if scanned % UPDATE_EVERY == 0 or scanned == total:
            frappe.cache().set_value("sandwich_status", {
                "status": "running",
                "processed": processed,
                "created": created,
                "cleared": cleared,
                "scanned": scanned,       # 👈 NEW
                "total": total,
                "current_employee": emp
            })
        frappe.db.commit()
    # -----------------------------------
    # FINAL RETURN
    # -----------------------------------
    return {
        "ok": True,
        "processed": processed,
        "created": created,
        "cleared": cleared,
        "scanned": scanned,
        "total": total
    }

@frappe.whitelist()
def get_sandwich_status():
    return frappe.cache().get_value("sandwich_status") or {}

def _has_shift_in_range(employee, from_date, to_date):
    return frappe.db.sql("""
        SELECT name
        FROM `tabShift Assignment`
        WHERE employee = %s
          AND docstatus = 1
          AND status = 'Active'
          AND start_date <= %s
          AND (
                end_date IS NULL
                OR end_date = ''
                OR end_date >= %s
              )
        LIMIT 1
    """, (employee, to_date, from_date))

# ------------------------------------------------------------
# Helpers: Holiday/WeeklyOff dates for employee
# ------------------------------------------------------------
def _get_holiday_dates_for_employee(employee, from_date, to_date):
    """
    Returns set(date) for all holiday + weekly-off dates.
    Best case: HRMS helper exists.
    Fallback: use Employee.holiday_list -> tabHoliday (weekly offs usually live there too).
    """
    from_date = getdate(from_date)
    to_date = getdate(to_date)

    # Try HRMS utility if available (version-dependent)
    try:
        from hrms.hr.doctype.holiday_list.holiday_list import get_holidays_for_employee
        hol = get_holidays_for_employee(employee, from_date, to_date)
        # logger.debug(f"Employee {employee}: fetched {len(hol or [])} holidays from HRMS utility")   
        # HRMS may return list[date] or list[dict]
        return {getdate(x["holiday_date"]) if isinstance(x, dict) else getdate(x) for x in hol}
    except Exception:
        pass

    # Fallback: Employee -> holiday_list
    holiday_list = frappe.db.get_value("Employee", employee, "holiday_list")
    if not holiday_list:
        return set()

    rows = frappe.get_all(
        "Holiday",
        filters={
            "parenttype": "Holiday List",
            "parent": holiday_list,
            "holiday_date": ("between", [from_date, to_date]),
        },
        pluck="holiday_date",
        limit_page_length=5000,
    )
    # logger.debug(f"[_get_holiday_dates_for_employee] Employee {employee}: fetched {len(rows)} holidays from fallback Holiday List {holiday_list}")        
    return {getdate(r) for r in rows}

# ------------------------------------------------------------
# Attendance helpers
# ------------------------------------------------------------
def _get_attendance_status(employee, date):
    status = frappe.db.get_value(
        "Attendance",
        {
            "employee": employee,
            "attendance_date": date,
            "docstatus": ("<", 2),
        },
        "status",
    )
    if not status:
        return "Absent"
    return status

# ------------------------------------------------------------
# Core sandwich logic
# A holiday / weekly off becomes sandwich only when it is between two absences.
    # Fri   Absent
    # Sat   Weekly Off
    # Sun   Weekly Off
    # Mon   Absent
    # Result:
        # Sat  Sandwich
        # Sun  Sandwich
    # Leave or Present breaks sandwich
    # Thu   Absent
    # Fri   Holiday
    # Sat   Weekly Off
    # Sun   Weekly Off
    # Mon   Absent
    # Output:
        # Fri  Sandwich
        # Sat  Sandwich
        # Sun  Sandwich
    # Thu   Absent
    # Fri   Holiday
    # Sat   Leave
    # Sun   Weekly Off
    # Mon   Absent
    # Result:
        # NO sandwich
# ------------------------------------------------------------
def _run_sandwich_for_employee(employee, from_date, to_date, hol_dates):
    # logging.debug(f"_run_sandwich_for_employee {employee} from {from_date} to {to_date} with holidays on {hol_dates}")   
    created = 0
    cleared = 0
    from_date = getdate(from_date)
    to_date = getdate(to_date)
    # ---------------------------------------------------
    # 🔥 Prefetch ALL attendance in one go
    # ---------------------------------------------------
    attendance_rows = frappe.get_all(
        "Attendance",
        filters={
            "employee": employee,
            "attendance_date": ("between", [add_days(from_date, -2), add_days(to_date, 2)]),
            "docstatus": ("<", 2),
        },
        fields=["attendance_date", "status"],
        limit_page_length=5000,
    )
    # Build map: date → status
    attendance_map = {
        getdate(r.attendance_date): r.status for r in attendance_rows
    }
    def get_status(d):
        return attendance_map.get(d, "Absent")
    # ---------------------------------------------------
    # Iterate once
    # ---------------------------------------------------
    d = from_date
    while d <= to_date:
        if d not in hol_dates:
            d = add_days(d, 1)
            continue
        # ---------------------------------------------------
        # Build contiguous holiday block
        # ---------------------------------------------------
        block_start = d
        block_end = d
        while add_days(block_end, 1) in hol_dates:
            block_end = add_days(block_end, 1)
        prev_day = add_days(block_start, -1)
        next_day = add_days(block_end, 1)
        prev_status = get_status(prev_day)
        next_status = get_status(next_day)
        # logger.debug(
        #     f"[SANDWICH] {employee} block {block_start}->{block_end} "
        #     f"prev={prev_status} next={next_status}"
        # )
        # ---------------------------------------------------
        # 🎯 CORE RULE
        # ---------------------------------------------------
        if prev_status == "Absent" and next_status == "Absent":
            # CREATE sandwich for full block
            x = block_start
            while x <= block_end:
                created += _apply_sandwich_day(employee, x)
                x = add_days(x, 1)
        else:
            # CLEAR sandwich for full block
            cleared += _clear_sandwich_block(employee, block_start, block_end)
        d = add_days(block_end, 1)
    return {"created": created, "cleared": cleared}

     

# ------------------------------------------------------------
# Apply sandwich leave
# ------------------------------------------------------------
def _apply_sandwich_day(employee, date):
    settings = frappe.get_cached_doc("VCM HR Settings")
    locked_upto = getdate(settings.attendance_locked_upto) if settings.attendance_locked_upto else None
    if locked_upto and date <= locked_upto:
        return 0
    # ------------------------------------------------------------
    # If attendance exists and employee worked enough → skip sandwich
    # ------------------------------------------------------------
    att = frappe.db.get_value(
        "Attendance",
        {
            "employee": employee,
            "attendance_date": date,
            "docstatus": 1
        },
        [
            "name",
            "custom_first_half_done",
            "custom_second_half_done",
            "status",
            "shift",
            "working_hours"
        ],
        as_dict=True
    )
    # logger.debug(f"_apply_sandwich_day: Attendance for {employee} on {date}: {att}")    
    if att:        
        if att.status != "Absent":
            # Attendance exists but is not Absent, like half day, On leave etc
            return 0        
        # Must have shift to decide logic
        if not att.shift:
            return 0
        shift = frappe.get_cached_doc("Shift Type", att.shift)
        if not shift:
            return 0  # cannot decide → safest is skip sandwich
        working_hours = flt(att.working_hours or 0) 
        # ------------------------------------------------------------
        # FLEXI vs NON-FLEXI handling
        # ------------------------------------------------------------ 
        # FLEXI SHIFT LOGIC               
        if cint(getattr(shift, "custom_flexible_shift", 0)):
            if working_hours == 0:
                return 0  # data not reliable yet
            # define half-day threshold (adjust if you store elsewhere)
            # fallback → derive from shift hours
            # ✅ FIX: ALWAYS DEFINE FIRST
            half_day_threshold = None
            # try configured threshold
            half_day_threshold = flt(getattr(shift, "working_hours_threshold_for_half_day", 0))
            # fallback
            if not half_day_threshold:
                total_hours = flt(getattr(shift, "working_hours", 0))
                half_day_threshold = total_hours / 2 if total_hours else 0
             # safety check
            if half_day_threshold and working_hours >= half_day_threshold:
                return 0  # worked enough → skip sandwich
        # ------------------------------------------------------------
        # NON-FLEX SHIFT LOGIC
        # ------------------------------------------------------------
        else:
            first_half = int(att.get("custom_first_half_done") or 0)
            second_half = int(att.get("custom_second_half_done") or 0)
            if first_half or second_half:
                return 0
    # ------------------------------------------------------------
    # Skip if leave already exists
    # ------------------------------------------------------------
    existing = frappe.get_all(
        "Leave Application",
        filters={
            "employee": employee,
            "leave_type": SANDWICH_LEAVE_TYPE,
            "from_date": date,
            "to_date": date,
            "docstatus": ("<", 2)
        },
        pluck="name",
    )
    if existing:
        # logger.debug(f"Sandwich leave already exists for {employee} on {date}, skipping creation")
        return 0
    # ------------------------------------------------------------
    # Create Sandwich Leave
    # ------------------------------------------------------------
    leave = frappe.get_doc(
        {
            "doctype": "Leave Application",
            "employee": employee,
            "leave_type": SANDWICH_LEAVE_TYPE,
            "from_date": date,
            "to_date": date,
            "status": "Approved",
            "description": "Auto Sandwich Leave",
        }
    )
    leave.insert(ignore_permissions=True)
    leave.submit()
    # logger.debug(f"Sandwich leave created for {employee} on {date}")
    return 1


# ------------------------------------------------------------
# Clear sandwich leave
# Sandwich rule is based on two boundary absences:
# Absent → Holiday → Holiday → Absent
# If either boundary stops being absent, sandwich must disappear.
# So the rule becomes:
# boundary_day_worked → clear sandwich
# ------------------------------------------------------------
def _clear_sandwich_block(employee, start, end):
    cleared = 0
    prev_day = add_days(start, -1)
    next_day = add_days(end, 1)
    prev_status = _get_attendance_status(employee, prev_day)
    next_status = _get_attendance_status(employee, next_day)
    # logger.debug(f"Clearing sandwich block for {employee} from {start} to {end}")    
    # ❌ Otherwise clear ALL sandwich leaves in block
    # -----------------------------------------------------
    # STEP 1: Delete sandwich leaves
    # -----------------------------------------------------
    leaves = frappe.get_all(
        "Leave Application",
        filters={
            "employee": employee,
            "leave_type": SANDWICH_LEAVE_TYPE,
            "from_date": ("<=", end),
            "to_date": (">=", start),
            "docstatus": 1,
        },
        pluck="name",
    )
    for name in leaves:
        try:
            doc = frappe.get_doc("Leave Application", name)
            doc.cancel()
            frappe.delete_doc("Leave Application", name, force=1)
            # logger.debug(
            #     f"[SANDWICH CLEARED ****] {employee} {start}->{end} "
            #     f"prev={prev_status} next={next_status}"
            # )
            cleared += 1
        except Exception:
            logger.exception(f"[sandwich clear failed] {name}")
    # -----------------------------------------------------
    # STEP 2: Process each date in block
    # -----------------------------------------------------
    now = now_datetime()
    total_days = date_diff(end, start) + 1
    for i in range(total_days):
        d = add_days(start, i)
        start_dt = get_datetime(d)
        end_dt = get_datetime(add_days(d, 1))
        # fetch checkins for that day
        checkins = frappe.get_all(
            "Employee Checkin",
            filters={
                "employee": employee,
                "time": ("between", [start_dt, end_dt]),
                "offshift": 0
            },
            fields=["name"],
        )
        # logger.debug(f"Checkin sandwich block for {checkins}")    
        if not checkins:
            continue
        # -------------------------------------------------
        # STEP 2A: unlink (important)
        # -------------------------------------------------
        _unlink_day_checkins(employee, d)
        # -------------------------------------------------
        # STEP 2B: mark for repair via modified
        # -------------------------------------------------
        for c in checkins:
            try:
                frappe.db.set_value(
                    "Employee Checkin",
                    c.name,
                    "modified",
                    now,
                    update_modified=True
                )
            except Exception:
                logger.exception(f"[CHECKIN TOUCH FAILED] {c.name}")

        # logger.info(
        #     f"[SANDWICH REPAIR MARKED] {employee} {d} "
        #     f"checkins={len(checkins)}"
        # )
    return cleared


# def clear_sandwich_if_checkin(employee, date):
#     start_dt = frappe.utils.get_datetime(date)
#     end_dt = frappe.utils.get_datetime(add_days(date, 1))
#     # -----------------------------------------------------
#     # STEP 0: Fetch checkins properly (NOT db.exists)
#     # -----------------------------------------------------
#     checkins = frappe.get_all(
#         "Employee Checkin",
#         filters={
#             "employee": employee,
#             "time": ("between", [start_dt, end_dt]),
#             "offshift": 0
#         },
#         fields=["name", "shift"],
#         order_by="time asc"
#     )
#     if not checkins:
#         return
#     shift = checkins[0].shift  # may be None (acceptable in your system)
#     # -----------------------------------------------------
#     # STEP 1: Delete sandwich leave
#     # -----------------------------------------------------
#     leaves = frappe.get_all(
#         "Leave Application",
#         filters={
#             "employee": employee,
#             "leave_type": SANDWICH_LEAVE_TYPE,
#             "from_date": ("<=", date),
#             "to_date": (">=", date),
#             "docstatus": 1
#         },
#         pluck="name"
#     )
#     for name in leaves:
#         try:
#             doc = frappe.get_doc("Leave Application", name)
#             doc.cancel()
#             frappe.delete_doc("Leave Application", doc.name, force=1)
#             logger.info(
#                 f"[SANDWICH CLEARED] {employee} {date} | leave={name}"
#             )
#         except Exception:
#             logger.exception(f"[SANDWICH CLEAR FAILED] {name}")
#     # -----------------------------------------------------
#     # STEP 2: Unlink checkins (CRITICAL for rebuild)
#     # -----------------------------------------------------
#     _unlink_day_checkins(employee, date)
#     logger.info(
#         f"[SANDWICH] Unlinked checkins for {employee} {date}"
#     )
#     # -----------------------------------------------------
#     # STEP 3: Trigger repair via modified timestamp
#     # (NO direct process_auto_attendance call)
#     # -----------------------------------------------------
#     now = now_datetime()
#     for c in checkins:

#         try:
#             frappe.db.set_value(
#                 "Employee Checkin",
#                 c.name,
#                 "modified",
#                 now,
#                 update_modified=True
#             )
#         except Exception:
#             logger.exception(f"[CHECKIN TOUCH FAILED] {c.name}")

#     logger.info(
#         f"[SANDWICH] Marked {len(checkins)} checkins for repair "
#         f"{employee} {date} shift={shift}"
#     )