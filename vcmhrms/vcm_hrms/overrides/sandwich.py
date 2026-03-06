import frappe
from frappe.utils import getdate, nowdate, add_days, cint
import logging
logger = logging.getLogger(__name__)

@frappe.whitelist()
def run_sandwich_daily():
    settings = frappe.get_cached_doc("VCM HR Settings")
    locked_upto = getdate(settings.attendance_locked_upto) if settings.attendance_locked_upto else None
    # Decide window: run for “open period” only
    start = add_days(locked_upto, 1) if locked_upto else add_days(getdate(nowdate()), -31)
    end = getdate(nowdate())
    run_sandwich_policy(from_date=start, to_date=end)


# ------------------------------------------------------------
# Helpers: Holiday/WeeklyOff dates for employee
# ------------------------------------------------------------
def _get_holiday_dates_for_employee(employee: str, from_date, to_date) -> set:
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
        out = set()
        for x in hol or []:
            if isinstance(x, dict):
                out.add(getdate(x.get("holiday_date")))
            else:
                out.add(getdate(x))
        return out
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
        fields=["holiday_date"],
        limit_page_length=5000,
    )
    # logger.debug(f"[_get_holiday_dates_for_employee] Employee {employee}: fetched {len(rows)} holidays from fallback Holiday List {holiday_list}")        
    return {getdate(r["holiday_date"]) for r in rows}


def _is_absent(att: dict | None) -> bool:
    """
    Sandwich counts ONLY strict Absent.
    IMPORTANT: Missing Attendance record counts as Absent (your requirement),
    so None => True.
    """
    if not att:
        return True
    return (att.get("status") or "").strip().lower() == "absent"


def _is_leave_driven(att: dict | None) -> bool:
    """
    Missing Attendance cannot be considered leave-driven.
    """
    if not att:
        return False
    status = (att.get("status") or "").strip().lower()
    return bool(att.get("leave_application") or att.get("leave_type") or status == "on leave")


# ------------------------------------------------------------
# Public runner
# ------------------------------------------------------------
@frappe.whitelist()
def run_sandwich_policy(from_date=None, to_date=None, employee=None):
    """
    Sandwich rule (always enabled):
    If employee is Absent on both sides of a Holiday/WeeklyOff block,
    then those Holiday/WeeklyOff dates become Absent (LWP) and we set:
        Attendance.custom_sandwich_applied = 1

    Auto-clear:
    If later punches/leave change the side days, re-running will clear sandwich flags
    and (optionally) cancel sandwich-created attendance docs.
    """
    to_date = getdate(to_date or nowdate())
    from_date = getdate(from_date or add_days(to_date, -31))
    # Respect attendance lock: never touch locked or earlier
    settings = frappe.get_cached_doc("VCM HR Settings")
    locked_upto = getdate(settings.attendance_locked_upto) if getattr(settings, "attendance_locked_upto", None) else None
    if locked_upto and from_date <= locked_upto:
        from_date = add_days(locked_upto, 1)
    if locked_upto and to_date <= locked_upto:
        return {"ok": True, "skipped": "range fully locked"}
    employees = [employee] if employee else frappe.get_all("Employee", filters={"status": "Active"}, pluck="name")
    totals = {"processed_employees": 0, "marked": 0, "cleared": 0, "range": [str(from_date), str(to_date)]}
    # logger.debug(f"[run_sandwich_policy 1] y for {len(employees)} employees, from {from_date} to {to_date}, locked_upto={locked_upto}")
    for emp in employees:
        totals["processed_employees"] += 1
        res = _run_sandwich_for_employee(emp, from_date, to_date)
        totals["marked"] += res["marked"]
        totals["cleared"] += res["cleared"]
    frappe.db.commit()
    return {"ok": True, **totals}


# ------------------------------------------------------------
# Core per-employee logic
# ------------------------------------------------------------
def _run_sandwich_for_employee(employee: str, from_date, to_date):
    # Need neighbor lookup, so scan with a buffer, considering 
    """
    p=None, n=Absent ⇒ treated as Absent/Absent ⇒ sandwich applies
    p=Absent, n=None ⇒ sandwich applies
    p=None, n=On Leave ⇒ leave-driven breaks ⇒ no sandwich
    p=Half Day, n=Absent ⇒ Half Day is not absent ⇒ no sandwich
    p=Absent, n=Present ⇒ no sandwich
    """
    buffer_days = 5
    scan_from = add_days(from_date, -buffer_days)
    scan_to = add_days(to_date, buffer_days)

    # Attendance map in scan range
    atts = frappe.get_all(
        "Attendance",
        filters={
            "employee": employee,
            "attendance_date": ("between", [scan_from, scan_to]),
            "docstatus": ("<", 2),
        },
        fields=[
            "name", "attendance_date", "status", "docstatus",
            "leave_application", "leave_type", "custom_sandwich_applied",
        ],
        limit_page_length=5000,
    )
    att_by_date = {getdate(a["attendance_date"]): a for a in atts}
    if (employee == "HP1671"):
        logger.debug(f"[_run_sandwich_for_employee] Employee {employee}: loaded {len(atts)} attendance records in scan range {scan_from}..{scan_to}")    

    hol_dates = _get_holiday_dates_for_employee(employee, scan_from, scan_to)
    # if (employee == "HP1671"):
    #     logger.debug(f"Employee {employee}: found {len(hol_dates)} holiday/weekly-off ")

    def is_non_working(d):
        return d in hol_dates

    def prev_working(d):
        x = add_days(d, -1)
        while x >= scan_from and is_non_working(x):
            x = add_days(x, -1)
        return x if x >= scan_from else None

    def next_working(d):
        x = add_days(d, 1)
        while x <= scan_to and is_non_working(x):
            x = add_days(x, 1)
        return x if x <= scan_to else None

    # Helper to resolve day status (treat missing as Absent)
    def _resolve_status(status_by_date, d):
        # Default: Absent if no Attendance record exists
        return (status_by_date.get(d) or "Absent")

    marked = cleared = 0
    d = from_date

    while d <= to_date:
        if not is_non_working(d):
            d = add_days(d, 1)
            continue

        # Find contiguous non-working block
        block_start = d
        block_end = d
        while add_days(block_end, 1) <= to_date and is_non_working(add_days(block_end, 1)):
            block_end = add_days(block_end, 1)
        p = prev_working(block_start)
        n = next_working(block_end)
        if not p or not n:
            d = add_days(block_end, 1)
            continue
        p_att = att_by_date.get(p)
        n_att = att_by_date.get(n)
        # Missing attendance on sides should count as Absent (do NOT skip).
        # But log for debug visibility.
        # if (employee == "HP1671") and (not p_att or not n_att):
        #     logger.debug(
        #         f"Employee {employee}: block {block_start}..{block_end} side attendance missing "
        #         f"(treated as Absent), p={p}({p_att.get('status') if p_att else 'None'}), "
        #         f"n={n}({n_att.get('status') if n_att else 'None'})"
        #     )

        # Leave-driven breaks sandwich
        if _is_leave_driven(p_att) or _is_leave_driven(n_att):
            cleared += _clear_sandwich_block(employee, block_start, block_end, att_by_date)
            if (employee == "HP1671"):
                logger.debug(f"[_run_sandwich_for_employee] Employee {employee}: block {block_start}..{block_end} is broken by leave-driven attendance, p={p}({p_att.get('status')}), n={n}({n_att.get('status')})") 
            d = add_days(block_end, 1)
            continue

        if _is_absent(p_att) and _is_absent(n_att):
            x = block_start
            while x <= block_end:
                marked += _apply_sandwich_day(employee, x, att_by_date)
                if (employee == "HP1671"):
                    logger.debug(f"[_run_sandwich_for_employee] Employee {employee}: marking {x} as Absent due to sandwich,") 
                x = add_days(x, 1)
        else:
            cleared += _clear_sandwich_block(employee, block_start, block_end, att_by_date)

        d = add_days(block_end, 1)
        # if (employee == "HP1671"):
        #     logger.debug(
        #         f"Employee {employee}: processed block {block_start}..{block_end}, "
        #         # f"prev={p}({p_att.get('status') if p_att else 'None'}), "
        #         # f"next={n}({n_att.get('status') if n_att else 'None'}), "
        #         f"marked={marked}, cleared={cleared}"
        #     )
    return {"marked": marked, "cleared": cleared}


# ------------------------------------------------------------
# Apply / Clear actions
# ------------------------------------------------------------
def _apply_sandwich_day(employee: str, d, att_by_date: dict) -> int:
    """
    If Attendance exists for that non-working day, mark it Absent + sandwich flag.
    If not, create submitted Attendance as Absent with sandwich flag.
    """
    a = att_by_date.get(d)

    if a and cint(a.get("custom_sandwich_applied") or 0) == 1 and (a.get("status") or "") == "Absent":
        return 0

    if a:
        # Never override leave-driven attendance
        if _is_leave_driven(a):
            return 0

        doc = frappe.get_doc("Attendance", a["name"])

        # Only touch if it's not already Absent sandwich
        changed = False
        if (doc.status or "") != "Absent":
            doc.status = "Absent"
            changed = True
        if cint(getattr(doc, "custom_sandwich_applied", 0) or 0) != 1:
            doc.custom_sandwich_applied = 1
            changed = True

        if changed:
            doc.save(ignore_permissions=True)
            if doc.docstatus == 0:
                doc.submit()

        att_by_date[d] = frappe.get_value(
            "Attendance", doc.name,
            ["name","attendance_date","status","docstatus","leave_application","leave_type","custom_sandwich_applied"],
            as_dict=True
        )
        return 1

    # Create new Attendance for that holiday/off day
    doc = frappe.get_doc({
        "doctype": "Attendance",
        "employee": employee,
        "attendance_date": d,
        "status": "Absent",
        "custom_sandwich_applied": 1,
        "custom_created_by_sandwich": 1,
    })
    doc.insert(ignore_permissions=True)
    doc.submit()

    att_by_date[d] = frappe.get_value(
        "Attendance", doc.name,
        ["name","attendance_date","status","docstatus","leave_application","leave_type","custom_sandwich_applied"],
        as_dict=True
    )
    return 1


def _clear_sandwich_block(employee: str, start, end, att_by_date: dict) -> int:
    """
    Clear sandwich-applied flag for non-working days in [start..end].
    Optionally cancel Attendance docs that were created only due to sandwich. "custom_created_by_sandwich": 1,
    (I’m keeping it SAFE: just clear the flag + leave status unchanged, unless you want cancel.)
    """
    cleared = 0
    x = start
    while x <= end:
        a = att_by_date.get(x)
        if a and cint(a.get("custom_sandwich_applied") or 0) == 1:
            doc = frappe.get_doc("Attendance", a["name"])
            created_by_sandwich = cint(getattr(doc, "custom_created_by_sandwich", 0) or 0)
            if created_by_sandwich and doc.docstatus == 1:
                # Safe revert: cancel the doc we created
                doc.cancel()
                att_by_date.pop(x, None)
            else:
                # Just clear the flag on existing records
                doc.custom_sandwich_applied = 0
                doc.save(ignore_permissions=True)
                att_by_date[x]["custom_sandwich_applied"] = 0
            cleared += 1
        x = add_days(x, 1)
    return cleared


# build a full calendar once per employee, to avoid repeated db hits in the core loop.
def _build_status_calendar(scan_start, scan_end, attendance_rows, doj=None, relieve=None):
    # default Absent for every day
    status_by_date = {}
    d = scan_start
    while d <= scan_end:
        # optionally ignore outside employment window
        if doj and d < doj:
            status_by_date[d] = None   # "ignore"
        elif relieve and d > relieve:
            status_by_date[d] = None   # "ignore"
        else:
            status_by_date[d] = "Absent"
        d = add_days(d, 1)
    # overlay actual attendances
    for r in attendance_rows:
        ad = r.attendance_date
        if ad in status_by_date and status_by_date[ad] is not None:
            status_by_date[ad] = r.status  # Present / Half Day / Absent / On Leave
    return status_by_date