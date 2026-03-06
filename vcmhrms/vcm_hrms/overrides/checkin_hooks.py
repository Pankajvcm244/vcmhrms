import frappe
from frappe.utils import get_datetime
from vcmhrms.vcm_hrms.overrides.VCMShiftType import _attendance_date_for_punch

def on_checkin_insert(doc, method=None):
    pass
#     """
#     Instant attendance repair when a new checkin arrives.
#     Only touches the affected employee-day.
#     """

#     if doc.skip_auto_attendance:
#         return

#     if doc.offshift:
#         return

#     if not doc.shift:
#         return

#     shift = frappe.get_cached_doc("Shift Type", doc.shift)
#     punch_dt = get_datetime(doc.time)
#     attendance_date = _attendance_date_for_punch(shift, punch_dt)
#     # safety: never touch locked days
#     settings = frappe.get_cached_doc("VCM HR Settings")
#     if settings.attendance_locked_upto and attendance_date <= settings.attendance_locked_upto:
#         return
#     # run targeted repair
#     _repair_single_day(doc.employee, attendance_date, doc.shift)


# def _repair_single_day(employee, attendance_date, shift_name):
#     shift = frappe.get_cached_doc("Shift Type", shift_name)
#     attendance = frappe.get_value(
#         "Attendance",
#         {
#             "employee": employee,
#             "attendance_date": attendance_date,
#             "docstatus": ("<", 2)
#         },
#         ["name", "docstatus"],
#         as_dict=True
#     )
#     if attendance:
#         if attendance.docstatus == 1:
#             frappe.get_doc("Attendance", attendance.name).cancel()
#     # unlink logs for that day
#     frappe.db.sql("""
#         UPDATE `tabEmployee Checkin`
#         SET attendance = NULL
#         WHERE employee=%s
#         AND shift=%s
#         AND DATE(time)=%s
#         AND skip_auto_attendance=0
#         AND offshift=0
#     """, (employee, shift_name, attendance_date))
#     # run HRMS for this shift only
#     shift.process_auto_attendance()