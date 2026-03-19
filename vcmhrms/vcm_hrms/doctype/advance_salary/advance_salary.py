# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt


from frappe.model.document import Document
import frappe
from datetime import datetime
from dateutil.relativedelta import relativedelta

import frappe
from frappe.model.document import Document
from frappe.utils import nowdate
# from frappe.model.naming import make_autoname
from frappe.utils.data import getdate,today

from frappe.model.naming import getseries



class AdvanceSalary(Document):

    def autoname(self):
        dateF = getdate(today()) #frappe.utils.today()
        year = dateF.strftime("%y")
        month = dateF.strftime("%m")
        prefix = f"ADV-REQ-{year}{month}-"
        self.name = prefix + getseries(prefix, 5)


    def validate(self):
        self.get_monthly_installments()
        self.set_payroll_dates()

    def on_submit(self):
        self.create_recovery_entries()

    def get_monthly_installments(self):
        if not self.advance_amount or not self.recovery_months:
            self.recovery_amount = 0
            return

        monthly_amount = float(self.advance_amount) / float(self.recovery_months)
        self.recovery_amount = round(monthly_amount, 2)

    def set_payroll_dates(self):
        month_map = {
            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
            "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
            "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
        }

        if not self.deduction_start_month or not self.recovery_months:
            return

        selected_month = month_map.get(self.deduction_start_month)

        today = datetime.now()
        year = today.year

        # If month already passed → next year
        if selected_month < today.month:
            year += 1

        # END DATE = 25th + recovery months - 1
        end_date = datetime(year, selected_month, 25) + relativedelta(
            months=int(self.recovery_months) - 1
        )

        # START DATE = 26th of previous month
        start_date = datetime(year, selected_month, 1) - relativedelta(days=1)
        start_date = start_date.replace(day=26)

        self.start_date = start_date
        self.end_date = end_date

        if not self.start_date or not self.end_date:
            frappe.throw("Start Date / End Date not set properly")

    def create_recovery_entries(self):
        additional_salary = frappe.get_doc({
            "doctype": "Additional Salary",
            "employee": self.employee,
            "salary_component": self.deduction_component,
            "amount": self.recovery_amount,
            "company": self.company,

            # Recurring
            "is_recurring": 1,
            "from_date": self.start_date,
            "to_date": self.end_date,

            "ref_doctype": self.doctype,
            "ref_docname": self.name,
			"docstatus": 1
        })

        additional_salary.insert(ignore_permissions=True)


 