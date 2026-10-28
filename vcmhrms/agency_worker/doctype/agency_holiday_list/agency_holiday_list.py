# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AgencyHolidayList(Document):
    def autoname(self):
        self.holiday_list_name=(self.holiday_list_name or '').strip()
        if not self.holiday_list_name:
            frappe.throw('Agency Holiday List name is required.')
        self.name=self.holiday_list_name

    def validate(self):
        self.holiday_list_name=(self.holiday_list_name or '').strip()
        if not self.holiday_list_name:
            frappe.throw('Agency Holiday List name is required.')
        if not self.holidays:
            frappe.throw('Add at least one Agency Holiday.')
        dates=[row.holiday_date for row in self.holidays if row.holiday_date]
        if len(dates)!=len(set(dates)):
            frappe.throw('An Agency Holiday List cannot contain the same date more than once.')
