# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import getdate, add_days
from frappe.model.document import Document
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
