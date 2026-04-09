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