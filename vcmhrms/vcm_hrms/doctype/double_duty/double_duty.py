# Copyright (c) 2026, Aman Soni and contributors
# For license information, please see license.txt


import frappe
from frappe.model.document import Document
from datetime import datetime
from frappe.model.naming import make_autoname


class DoubleDuty(Document):

    def autoname(self):
        now = datetime.now()
        mmyy = now.strftime("%y%m")
        self.name = make_autoname(f"DD-{mmyy}-.####")


    #  VALIDATE →Duplicate Check
    def validate(self):
        for row in self.employee_details:
            if not row.employee or not row.amount:
                continue

            if frappe.db.exists("Additional Salary", {
                "employee": row.employee,
                "payroll_date": self.payroll_date,
                "salary_component": self.salary_component,
            }):
                frappe.throw(
                    f"Additional Salary already exists for Employee: {row.employee}"
                )


    #  ON SUBMIT  Only Create
    def on_submit(self):
        for row in self.employee_details:
            if not row.employee or not row.amount:
                continue

            additional_salary = frappe.get_doc({
                "doctype": "Additional Salary",
                "employee": row.employee,
                "company": self.company,
                "payroll_date": self.payroll_date,
                "salary_component": self.salary_component,
                "amount": row.amount,
                "overwrite_salary_structure_amount": 0,
                "ref_docname": self.name,
                "ref_doctype": self.doctype
            })

            additional_salary.insert(ignore_permissions=True)
            additional_salary.submit()


    def on_cancel(self):
        additional_salaries = frappe.get_all(
            "Additional Salary",
            filters={"ref_docname": self.name},
            pluck="name"
        )

        for sal in additional_salaries:
            doc = frappe.get_doc("Additional Salary", sal)

            if doc.docstatus == 1:
                doc.cancel()