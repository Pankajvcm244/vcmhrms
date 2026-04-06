# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt
import frappe
from frappe.utils import getdate, nowdate, add_days, get_datetime
import logging
logger = logging.getLogger(__name__)
SANDWICH_LEAVE_TYPE = "Sandwich Absent"

# ------------------------------------------------------------
# Night scheduler entry
# ------------------------------------------------------------
@frappe.whitelist()
def run_sandwich_daily():
    settings = frappe.get_cached_doc("VCM HR Settings")
    locked_upto = getdate(settings.attendance_locked_upto) if settings.attendance_locked_upto else None
    today = getdate(nowdate())
    start = add_days(today, -5)   # safe default window if locked_upto is blank
    if locked_upto:
        start = add_days(locked_upto, 1)
    if locked_upto and start <= locked_upto:
        start = add_days(locked_upto, 1)
    if start > today:
        return {
            "ok": True,
            "processed": 0,
            "created": 0,
            "cleared": 0,
            "skipped": "range fully locked"
        }
    return run_sandwich_policy(start, today)


# ------------------------------------------------------------
# Main  runner where we do everything in one go for an employee 
# ------------------------------------------------------------
@frappe.whitelist()
def run_sandwich_policy(from_date=None, to_date=None, employee=None):
    """
    Optimized Sandwich Rule
    If employee is Absent on both sides of a Holiday/WeeklyOff block,
    then those Holiday/WeeklyOff dates become Absent.
    Improvements:
    - Only process employees with activity
    - Skip employees without holidays
    - Minimal attendance queries
    """
    to_date = getdate(to_date or nowdate())
    from_date = getdate(from_date)
    # Respect attendance lock: never touch locked or earlier
    settings = frappe.get_cached_doc("VCM HR Settings")
    locked_upto = getdate(settings.attendance_locked_upto) if getattr(settings, "attendance_locked_upto", None) else None
    if locked_upto and from_date <= locked_upto:
        from_date = add_days(locked_upto, 1)
    if locked_upto and to_date <= locked_upto:
        return {"ok": True, "skipped": "range fully locked"}
    employees = [employee] if employee else frappe.get_all(
        "Employee",
        filters={"status": "Active"},
        pluck="name"
    )
    created = 0
    cleared = 0
    # logger.debug(f"[run_sandwich_policy 1] y for {len(employees)} employees, from {from_date} to {to_date}, locked_upto={locked_upto}")
    for emp in employees:
        # Skip if employee has no holiday in this range
        hol_dates = _get_holiday_dates_for_employee(emp, from_date, to_date)
        if not hol_dates:
            continue
        res = _run_sandwich_for_employee(emp, from_date, to_date, hol_dates)
        created += res["created"]
        cleared += res["cleared"]    
    frappe.db.commit()
    return {
        "ok": True,
        "processed": created + cleared,
        "created": created,
        "cleared": cleared
    }



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
        logger.debug(
            f"[SANDWICH] {employee} block {block_start}->{block_end} "
            f"prev={prev_status} next={next_status}"
        )
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
            "status"
        ],
        as_dict=True
    )
    # logger.debug(f"_apply_sandwich_day: Attendance for {employee} on {date}: {att}")
    if att:
        if att.status != "Absent":
            # Attendance exists but is Absent
            # Attendance exists but no work done
            return 0
        first_half = int(att.get("custom_first_half_done") or 0)
        second_half = int(att.get("custom_second_half_done") or 0)
        # If any half done → employee worked enough
        if first_half or second_half:
            # logger.debug(
            #     f"Sandwich skipped for {employee} on {date} "
            #     f"(first_half_done={first_half}, second_half_done={second_half})"
            # )
            return 0
        # If attendance exists but not enough work 
        # Lets create sandwich if none of half is done
        # return 0
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
            logger.info(
                f"[sandwich cleared] {employee} {start}->{end} "
                f"prev={prev_status} next={next_status}"
            )
            cleared += 1
        except Exception:
            logger.exception(f"[sandwich clear failed] {name}")
    return cleared


def clear_sandwich_if_checkin(employee, date):
    start_dt = frappe.utils.get_datetime(date)
    end_dt = frappe.utils.get_datetime(add_days(date, 1))
    checkin = frappe.db.exists(
        "Employee Checkin",
        {
            "employee": employee,
            "time": ("between", [start_dt, end_dt])
        }
    )
    if not checkin:
        return
    leaves = frappe.get_all(
        "Leave Application",
        filters={
            "employee": employee,
            "leave_type": SANDWICH_LEAVE_TYPE,
            "from_date": ("<=", date),
            "to_date": (">=", date),
            "docstatus": 1
        },
        pluck="name"
    )
    for name in leaves:
        doc = frappe.get_doc("Leave Application", name)
        doc.cancel()
        frappe.delete_doc(
            "Leave Application",
            doc.name,
            force=1
        )
        logger.info(f"Sandwich leave cancelled due to checkin: {employee} {date}")

