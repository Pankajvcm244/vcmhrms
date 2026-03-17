# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
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
        frappe.db.set_value(
            "Attendance",
            att_doc.name,
            {
                "status": "Half Day",
                "leave_type": doc.leave_type,
                "leave_application": doc.name,
                "half_day_status": "Absent",
            },
        )
    else:
        # logger.debug(f"Attendance {att_doc.name} is not submitted, modifying by saving directly.")
        att_doc.status = "Half Day"
        att_doc.leave_type = doc.leave_type
        att_doc.leave_application = doc.name
        att_doc.half_day_status = "Absent"
        att_doc.save(ignore_permissions=True)