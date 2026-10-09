from frappe.model.document import Document
import frappe
from frappe.model.naming import make_autoname
from frappe.utils import get_datetime


class TempPunch(Document):
    def autoname(self):
        code=frappe.db.get_value('Temp Worker',self.worker,'staff_id')
        if not code or not self.punch_time: frappe.throw('Staff ID and Punch Time are required for naming.')
        self.staff_id=code
        self.name=make_autoname(f'TempPunch-{get_datetime(self.punch_time):%y%m%d}-.######')

    def validate(self):
        if not self.is_new() or not self.flags.workforce_ingestion:
            frappe.throw('Original punches can only be inserted through the SmartOffice API.')
        self.staff_id=frappe.db.get_value('Temp Worker',self.worker,'staff_id')

    def on_trash(self):
        frappe.throw('Original punches cannot be deleted.')
