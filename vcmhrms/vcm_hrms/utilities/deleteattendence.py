import frappe
from frappe.utils import getdate, today

# bench --site erp.vcmerp.in execute vcmhrms.vcm_hrms.utilities.deleteattendence.delete_all_attendance_from_date --kwargs "{'from_date':'2025-12-26','dry_run':0}"

def delete_all_attendance_from_date(
    from_date: str = "2025-12-26",
    to_date: str = "2026-12-26",
    commit_every: int = 200,
    dry_run: int = 1,
):
    """
    Delete ALL Attendance records where attendance_date >= from_date
    (and <= to_date if provided), across all companies/employees.

    - Cancels submitted Attendance (docstatus=1) before deletion.
    - Deletes draft (docstatus=0) directly.
    """

    fd = getdate(from_date)
    td = getdate(to_date) if to_date else getdate(today())

    names = frappe.db.sql(
        """
        SELECT name
        FROM `tabAttendance`
        WHERE attendance_date BETWEEN %s AND %s
        ORDER BY attendance_date, employee
        """,
        (fd, td),
        as_dict=False,
    )
    names = [n[0] for n in names]

    frappe.msgprint(f"Found {len(names)} Attendance records between {fd} and {td} (dry_run={dry_run}).")

    if dry_run:
        sample = names[:30]
        frappe.msgprint("Sample (first 30): " + (", ".join(sample) if sample else "None"))
        return {"count": len(names), "sample": sample}

    deleted = 0
    for name in names:
        doc = frappe.get_doc("Attendance", name)

        # Cancel if submitted
        if doc.docstatus == 1:
            doc.cancel()

        # Delete
        frappe.delete_doc("Attendance", name, force=1, ignore_permissions=True)
        deleted += 1

        if deleted % commit_every == 0:
            frappe.db.commit()

    frappe.db.commit()
    frappe.msgprint(f"Deleted {deleted} Attendance records.")
    return {"deleted": deleted}





#   run this from console to delete all attendance records and rebuild auto attendance for all shifts. Use with caution, ideally on a test environment first. 
import frappe
from frappe.utils import now_datetime

print("=" * 80)
print("FULL ATTENDANCE CLEANUP - ALL DATES / ALL SHIFTS")
print("=" * 80)

# ---------------------------------------------------------
# STEP 1: Unlink ALL Employee Checkins from Attendance
# ---------------------------------------------------------
print("Unlinking all Employee Checkins...")

frappe.db.sql("""
    UPDATE `tabEmployee Checkin`
    SET attendance = NULL
    WHERE attendance IS NOT NULL
""")

frappe.db.commit()
print("All Employee Checkins unlinked.")

# ---------------------------------------------------------
# STEP 2: Get ALL Attendance records except already cancelled
# ---------------------------------------------------------
attendance_names = frappe.get_all(
    "Attendance",
    filters={
        "docstatus": ["!=", 2],
    },
    pluck="name",
    order_by="attendance_date asc, employee asc",
)

print("Attendance records found:", len(attendance_names))

# ---------------------------------------------------------
# STEP 3: Cancel submitted Attendance and delete all
# ---------------------------------------------------------
deleted = 0
failed = []

for idx, att_name in enumerate(attendance_names, start=1):
    try:
        att = frappe.get_doc("Attendance", att_name)

        if att.docstatus == 1:
            att.cancel()

        frappe.delete_doc("Attendance", att.name, force=1)
        deleted += 1

        if idx % 100 == 0:
            frappe.db.commit()
            print("Deleted:", idx)

    except Exception:
        frappe.db.rollback()
        failed.append(att_name)
        frappe.log_error(
            title=f"Attendance Delete Failed: {att_name}",
            message=frappe.get_traceback()
        )

frappe.db.commit()

print("=" * 80)
print("Attendance deleted:", deleted)
print("Delete failed:", len(failed))
if failed:
    print("First failed records:", failed[:20])
print("=" * 80)

# ---------------------------------------------------------
# STEP 4: Also unlink any remaining checkins, just in case
# ---------------------------------------------------------
frappe.db.sql("""
    UPDATE `tabEmployee Checkin`
    SET attendance = NULL
""")

frappe.db.commit()
print("All Employee Checkins attendance links cleared.")

# ---------------------------------------------------------
# STEP 5: Rebuild auto attendance for all enabled shifts
# ---------------------------------------------------------
# shift_names = frappe.get_all(
#     "Shift Type",
#     filters={
#         "enable_auto_attendance": 1,
#     },
#     pluck="name",
#     order_by="name asc",
# )

# print("Enabled shifts found:", len(shift_names))

# rebuilt = []
# rebuild_failed = []

# for shift_name in shift_names:
#     try:
#         print("Rebuilding shift:", shift_name)

#         shift = frappe.get_doc("Shift Type", shift_name)

#         # Your override handles missing last_sync_of_checkin,
#         # but setting it explicitly avoids HRMS skipping the shift.
#         if not shift.last_sync_of_checkin:
#             shift.last_sync_of_checkin = now_datetime()

#         frappe.flags.in_auto_attendance_run = True
#         shift.process_auto_attendance()
#         frappe.flags.in_auto_attendance_run = False

#         frappe.db.commit()
#         rebuilt.append(shift_name)

#     except Exception:
#         frappe.flags.in_auto_attendance_run = False
#         frappe.db.rollback()
#         rebuild_failed.append(shift_name)
#         frappe.log_error(
#             title=f"Auto Attendance Rebuild Failed: {shift_name}",
#             message=frappe.get_traceback()
#         )

# print("=" * 80)
# print("Shifts rebuilt:", len(rebuilt))
# print("Rebuild failed:", len(rebuild_failed))
# if rebuild_failed:
#     print("Failed shifts:", rebuild_failed)
# print("=" * 80)

# ---------------------------------------------------------
# STEP 6: Recalculate late grace quota after fresh rebuild
# ---------------------------------------------------------
# try:
#     from vcmhrms.vcm_hrms.overrides.VCMShiftType import run_late_grace_quota_background

#     print("Running late grace quota recalculation...")
#     run_late_grace_quota_background()
#     frappe.db.commit()
#     print("Late grace quota recalculation completed.")

# except Exception:
#     frappe.db.rollback()
#     frappe.log_error(
#         title="Late Grace Quota Recalculation Failed",
#         message=frappe.get_traceback()
#     )
#     print("Late grace quota recalculation failed. Check Error Log.")

# print("DONE.")