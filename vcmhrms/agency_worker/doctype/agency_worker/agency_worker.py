# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

from frappe.model.document import Document
import frappe
import re
from frappe.model.naming import make_autoname
from frappe.utils import getdate, now_datetime
from vcmhrms.agency_worker.service import check_budget_location


class AgencyWorker(Document):
    def autoname(self):
        self.name=make_autoname(f'AW-{now_datetime():%y%m}-.#####')

    def before_validate(self):
        if not self.is_new():
            return
        # Frappe's naming-series counter is database-backed and concurrency-safe.
        # Skip any values already present from an earlier manual numbering phase.
        while True:
            biometric_code=make_autoname('9.######')
            if not frappe.db.exists('Agency Worker',{'smartoffice_id':biometric_code}):
                self.smartoffice_id=biometric_code
                break

    def validate(self):
        check_budget_location(self.budget_location)
        self.staff_id=(self.staff_id or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9_/-]{1,40}',self.staff_id):
            frappe.throw('Staff ID may contain letters, numbers, hyphen, underscore or slash (maximum 40 characters).')
        self.smartoffice_id=(self.smartoffice_id or '').strip()
        if not re.fullmatch(r'9\d{5,6}',self.smartoffice_id):
            frappe.throw('Biometric Code must contain 6 or 7 digits and start with 9.')
        if self.active_until and getdate(self.active_until)<getdate(self.active_from):
            frappe.throw('Active Until cannot precede Active From.')
        for field,label in (('roster_managers','Roster Managers'),('supervisors','Supervisors')):
            users=[row.user for row in self.get(field) if row.user]
            if len(users)!=len(set(users)):
                frappe.throw(f'{label} cannot contain the same user more than once.')

