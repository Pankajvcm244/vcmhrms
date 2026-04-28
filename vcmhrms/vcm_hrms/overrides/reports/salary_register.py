# Copyright (c) 2026, Pankaj Sharma
# For license information, please see license.txt


from hrms.payroll.report.salary_register.salary_register import execute as original_execute
from vcmhrms.vcm_hrms.utilities.access_guard import validate_payroll_access

def execute(filters=None):
    validate_payroll_access()
    return original_execute(filters)