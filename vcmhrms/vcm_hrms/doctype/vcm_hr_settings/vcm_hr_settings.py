# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import getdate, add_days
from frappe.model.document import Document

from frappe.utils import now_datetime

import logging
logger = logging.getLogger(__name__)


class VCMHRSettings(Document):
	def validate(self):
		old_lock = frappe.db.get_value(
			"VCM HR Settings",
			self.name,
			"attendance_locked_upto"
		)
		if old_lock and self.attendance_locked_upto:
			if getdate(self.attendance_locked_upto) < getdate(old_lock):
				roles = frappe.get_roles()
				if not any(r in roles for r in ("System Manager", "Administrator")):
					frappe.throw("You cannot reduce Attendance Lock date.")

	def on_update(self):
		if not self.attendance_locked_upto:
			return
		# run only if lock date changed
		if not self.has_value_changed("attendance_locked_upto"):
			return
		start = add_days(getdate(self.attendance_locked_upto), 1)
		logger.debug(f"Updating Shift Types to {start} ")	
		frappe.db.sql("""
			UPDATE `tabShift Type`
			SET process_attendance_after = %s
		""", (start,))
		# clear cached Shift Types so HRMS scheduler reads new value
		frappe.clear_cache(doctype="Shift Type")



CACHE_KEY_DELETE_ALL_ATTENDANCE = "vcmhrms:delete_all_attendance_status"


def _set_delete_all_attendance_status(status, message=None, **kwargs):
    payload = {
        "status": status,
        "message": message or status,
        "updated_at": str(now_datetime()),
    }
    payload.update(kwargs or {})
    frappe.cache().set_value(CACHE_KEY_DELETE_ALL_ATTENDANCE, payload)


@frappe.whitelist()
def get_delete_all_attendance_status():
    return frappe.cache().get_value(CACHE_KEY_DELETE_ALL_ATTENDANCE) or {}


@frappe.whitelist()
def enqueue_delete_all_attendance():
    """
    Button action from VCM HR Settings.

    Deletes all Attendance records and unlinks all Employee Checkins.
    Intended only for test systems.
    """

    frappe.only_for(("System Manager", "HR Manager"))

    status = frappe.cache().get_value(CACHE_KEY_DELETE_ALL_ATTENDANCE) or {}
    if status.get("status") in ("Queued", "Running"):
        frappe.throw(
            "Attendance deletion is already queued/running.",
            title="Already Running"
        )

    _set_delete_all_attendance_status(
        "Queued",
        "Queued attendance deletion job..."
    )

    frappe.enqueue(
        "vcmhrms.vcm_hrms.doctype.vcm_hr_settings.vcm_hr_settings.delete_all_attendance_background",
        queue="long",
        timeout=60 * 60 * 3,
        job_name="Delete All Attendance",
        enqueue_after_commit=True,
    )

    return {
        "queued": True
    }


def delete_all_attendance_background():
    """
    Background job:
    1. Unlink all Employee Checkins from Attendance
    2. Cancel + delete all non-cancelled Attendance
    3. Clear Employee Checkin attendance links again
    """

    lock_key = "vcmhrms:delete_all_attendance_lock"

    with frappe.cache().lock(lock_key, timeout=60 * 60 * 3):
        try:
            _set_delete_all_attendance_status(
                "Running",
                "Unlinking all Employee Checkins from Attendance..."
            )

            frappe.db.sql("""
                UPDATE `tabEmployee Checkin`
                SET attendance = NULL
                WHERE attendance IS NOT NULL
            """)
            frappe.db.commit()

            attendance_names = frappe.get_all(
                "Attendance",
                filters={
                    "docstatus": ["!=", 2],
                },
                pluck="name",
                order_by="attendance_date asc, employee asc",
            )

            total = len(attendance_names)

            _set_delete_all_attendance_status(
                "Running",
                f"Attendance records found: {total}. Starting delete...",
                total=total,
                deleted=0,
                failed=0,
            )

            deleted = 0
            failed = []

            for idx, att_name in enumerate(attendance_names, start=1):
                try:
                    att = frappe.get_doc("Attendance", att_name)

                    if att.docstatus == 1:
                        att.cancel()

                    frappe.delete_doc(
                        "Attendance",
                        att.name,
                        force=1,
                        ignore_permissions=True,
                    )

                    deleted += 1

                    if idx % 50 == 0:
                        frappe.db.commit()

                        _set_delete_all_attendance_status(
                            "Running",
                            f"Deleted {idx} of {total} Attendance records...",
                            total=total,
                            deleted=deleted,
                            failed=len(failed),
                        )

                except Exception:
                    frappe.db.rollback()
                    failed.append(att_name)

                    frappe.log_error(
                        title=f"Attendance Delete Failed: {att_name}",
                        message=frappe.get_traceback()
                    )

            frappe.db.commit()

            _set_delete_all_attendance_status(
                "Running",
                "Clearing remaining Employee Checkin attendance links..."
            )

            frappe.db.sql("""
                UPDATE `tabEmployee Checkin`
                SET attendance = NULL
            """)
            frappe.db.commit()

            if failed:
                _set_delete_all_attendance_status(
                    "Completed",
                    f"Completed with warnings. Deleted {deleted}/{total}. Failed: {len(failed)}.",
                    total=total,
                    deleted=deleted,
                    failed=len(failed),
                    first_failed_attendance=failed[:20],
                )
            else:
                _set_delete_all_attendance_status(
                    "Completed",
                    f"Completed successfully. Deleted {deleted} Attendance records.",
                    total=total,
                    deleted=deleted,
                    failed=0,
                    first_failed_attendance=[],
                )

        except Exception:
            frappe.db.rollback()

            frappe.log_error(
                title="Delete All Attendance Failed",
                message=frappe.get_traceback()
            )

            _set_delete_all_attendance_status(
                "Failed",
                "Attendance deletion failed. Check Error Log."
            )