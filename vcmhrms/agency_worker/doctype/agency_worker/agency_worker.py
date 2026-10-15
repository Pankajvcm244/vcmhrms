from frappe.model.document import Document
import frappe
import re
from frappe.model.naming import make_autoname
from frappe.utils import getdate, now_datetime
from vcmhrms.agency_worker.service import check_budget_location


class AgencyWorker(Document):
    def autoname(self):
        self.name=make_autoname(f'AW-{now_datetime():%y%m}-.#####')

    def validate(self):
        check_budget_location(self.budget_location)
        self.staff_id=(self.staff_id or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',self.staff_id):
            frappe.throw('Staff ID must contain only letters, numbers, hyphen or underscore (maximum 40 characters).')
        if not self.smartoffice_id.isdigit() or not self.smartoffice_id.startswith('9'):
            frappe.throw('SmartOffice ID must be numeric and start with 9.')
        if self.active_until and getdate(self.active_until)<getdate(self.active_from):
            frappe.throw('Active Until cannot precede Active From.')
        for field,label in (('roster_managers','Roster Managers'),('supervisors','Supervisors')):
            users=[row.user for row in self.get(field) if row.user]
            if len(users)!=len(set(users)):
                frappe.throw(f'{label} cannot contain the same user more than once.')

