# Copyright (c) 2026, Pankaj Sharma
# For license information, please see license.txt


# vcmhrms/vcm_hrms/overrides/salary_slip.py
import frappe
from vcmhrms.vcm_hrms.utilities.access_guard import validate_payroll_access

def has_permission(doc, ptype, user):
    validate_payroll_access()
    return True


from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip
from vcmhrms.vcm_hrms.utilities.access_guard import validate_payroll_access

class CustomSalarySlip(SalarySlip):
    def has_permission(self, ptype="read", user=None):
        user = user or frappe.session.user
        validate_payroll_access()
        return super().has_permission(ptype)
    
   