# Copyright (c) 2026, Pankaj Sharma
# For license information, please see license.txt

# vcmhrms/vcm_hrms/utilities/access_guard.py
import frappe
import logging
logger = logging.getLogger(__name__)
CONFIDENTIAL_PAYROLL_USERS = {
    "teena.arora@vcm.org.in",
    # "sahil.chitkara@vcm.org.in",
    "pankaj.sharma@vcm.org.in",
    "Administrator",
}
CONFIDENTIAL_PAYROLL_REPORTS = {
    "Salary Register",
    "Income Tax Computation",
    "Income Tax Deductions",
    "Professional Tax Deductions",
    "Provident Fund Deductions",
    "Salary Payments via ECS",
    "Salary Payments Based On Payment Mode",
    "Bank Remittance",
}
CONFIDENTIAL_PAYROLL_DOCTYPES = {
    "Additional Salary",
    "Salary Slip",   
    "Salary Detail",
    "Salary Structure",
    "Salary Structure Assignment",   
    
    "Retention Bonus",
    "Payroll Entry",
    "Payroll Employee Detail",
    "Employee Tax Exemption Declaration",
    "Employee Tax Exemption Proof Submission",
    "Employee Benefit Application",
    "Employee Benefit Claim",
    "Employee Advance",
    "Employee Incentive",
}

def get_allowed_payroll_users():
    # logger.debug(f"in get_allowed_payroll_users ")
    # Optional: move to System Settings / doctype later
    return list(CONFIDENTIAL_PAYROLL_USERS)

def is_payroll_user(user=None):
    user = user or frappe.session.user
    # logger.debug(f"in is_payroll_user   user={user}")
    if user == "Administrator":
        return True
    return user in CONFIDENTIAL_PAYROLL_USERS

def validate_payroll_access(user=None):
    # logger.debug(f"in validate_payroll_access user={user}")
    user = user or frappe.session.user
    if not is_payroll_user(user):
        frappe.throw(
            "You are not authorized to access confidential  payroll data.",
            frappe.PermissionError
        )

def payroll_permissions_query_conditions(user):
    # logger.debug(f"in payroll_permissions_query_conditions   user={user}")
    """
    Blocks list view, report builder, link search, exports etc.
    for users who are not explicitly allowed.
    """
    if not is_payroll_user(user):
        return "1=0"
    return ""

def has_confidential_payroll_permission(doc, user=None, permission_type=None):
    """
    Blocks form open/read/print/email/export access.
    """
    user = user or frappe.session.user
    if is_payroll_user(user):
        return True
    return False