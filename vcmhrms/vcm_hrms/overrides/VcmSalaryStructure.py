import frappe
from hrms.payroll.doctype.salary_structure.salary_structure import SalaryStructure

class VcmSalaryStructure(SalaryStructure):

    def validate(self):
        super.validate()
        total = 0

        for row in self.earnings:
            if row.amount:
                total += row.amount

        self.custom_gross_salary = total