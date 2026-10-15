# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Agency(Document):
    def validate(self):
        if self.agency_holiday_list and frappe.db.get_value(
            'Agency Holiday List',self.agency_holiday_list,'disabled'
        ):
            frappe.throw('Select an active Agency Holiday List.')
