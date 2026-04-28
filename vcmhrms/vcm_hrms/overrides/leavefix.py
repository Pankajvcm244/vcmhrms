# Copyright (c) 2026, Pankaj Sharma
# For license information, please see license.txt


import frappe
from frappe.utils import getdate
import logging
logger = logging.getLogger(__name__)

def fix_existing_attendance(doc, method):
    # never allow Leave Application to create attendance if attendance already exists.
    # Only handle half-day leave case, as full day leave will not create attendance record at all.
    # logger.debug(f"Checking for existing attendance for Leave Application {doc.name} of employee {doc.employee} {doc.half_day}  on date {doc.from_date}")
    if not doc.half_day:
        # logger.debug("Not a half-day leave, skipping attendance fix.")
        return
    
    attendance = frappe.db.get_value(
        "Attendance",
        {
            "employee": doc.employee,
            "attendance_date": doc.from_date,
            "docstatus": 1
        },
        ["name", "status"],
        as_dict=True,
    )
    if not attendance:
        # logger.debug("No existing attendance found, skipping attendance fix.")
        return
    att_doc = frappe.get_doc("Attendance", attendance.name)
    # Before modifying attendance, confirm it is not already linked to another leave:
    if att_doc.leave_application and att_doc.leave_application != doc.name:
        frappe.throw(
            f"Attendance already linked to Leave {att_doc.leave_application}"
        )
    # Cancel if submitted
    if att_doc.docstatus == 1:
        # logger.debug(f"Attendance {att_doc.name} is submitted, direct DB modification.")
        # DO NOT force half_day_status here
        # Let apply_nonflex_strict_hours decide based on punch
        frappe.db.set_value(
            "Attendance",
            att_doc.name,
            {
                "status": "Half Day",
                "leave_type": doc.leave_type,
                "leave_application": doc.name,
            },
        )
    else:
        # logger.debug(f"Attendance {att_doc.name} is not submitted, modifying by saving directly.")
        att_doc.status = "Half Day"
        att_doc.leave_type = doc.leave_type
        att_doc.leave_application = doc.name
        att_doc.half_day_status = "Absent"
        att_doc.save(ignore_permissions=True)


def validate_locked_attendance(doc, method=None):
    settings = frappe.get_cached_doc("VCM HR Settings")
    if not getattr(settings, "attendance_locked_upto", None):
        return
    locked_upto = getdate(settings.attendance_locked_upto)
    from_date = getdate(doc.from_date) if doc.from_date else None
    to_date = getdate(doc.to_date) if doc.to_date else None
    half_day_date = getdate(doc.half_day_date) if doc.half_day_date else None
    # ------------------------------------------
    # FULL RANGE CHECK
    # ------------------------------------------
    if from_date and from_date <= locked_upto:
        frappe.throw(
            f"Cannot apply Leave before or on {locked_upto} (Attendance Locked)"
        )
    if to_date and to_date <= locked_upto:
        frappe.throw(
            f"Cannot apply Leave before or on {locked_upto} (Attendance Locked)"
        )
    # ------------------------------------------
    # HALF DAY CASE
    # ------------------------------------------
    if doc.half_day and half_day_date:
        if half_day_date <= locked_upto:
            frappe.throw(
                f"Cannot apply Half-Day Leave on {half_day_date} (Attendance Locked)"
            )