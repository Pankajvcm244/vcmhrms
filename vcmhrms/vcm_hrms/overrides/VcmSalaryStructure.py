import frappe
from hrms.payroll.doctype.salary_structure.salary_structure import SalaryStructure
from frappe.utils import flt
class VcmSalaryStructure(SalaryStructure):

    def validate(self):
        super().validate()
        total = 0
        for row in (self.earnings or []):
            total += flt(row.amount)

        self.custom_gross_salary = round(total, 2)