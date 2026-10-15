import frappe
from frappe.model.document import Document


class AgencyHolidayList(Document):
    def validate(self):
        dates=[row.holiday_date for row in self.holidays if row.holiday_date]
        if len(dates)!=len(set(dates)):
            frappe.throw('An Agency Holiday List cannot contain the same date more than once.')
