

# vcmhrms/vcm_hrms/utilities/access_guard.py

import frappe
import logging
logger = logging.getLogger(__name__)

def get_allowed_payroll_users():
    # logger.debug(f"in get_allowed_payroll_users ")
    # Optional: move to System Settings / doctype later
    return [
        "sahil.chitkara@vcm.org.in",
        "pankaj.sharma@vcm.org.in",
        "Administrator",
        "teena.arora@vcm.org.in",
    ]

def is_payroll_user(user=None):
    user = user or frappe.session.user
    # logger.debug(f"in is_payroll_user   user={user}")
    return user in get_allowed_payroll_users()

def validate_payroll_access(user=None):
    logger.debug(f"in validate_payroll_access user={user}")
    if not is_payroll_user(user):
        frappe.throw(
            "You are not authorized to access payroll data.",
            frappe.PermissionError
        )


def payroll_permissions_query_conditions(user):
    # logger.debug(f"in payroll_permissions_query_conditions   user={user}")
    if not is_payroll_user(user):
        # logger.debug(f"in payroll_permissions_query_conditions  NO ACCESS  user={user}")
        return "1=0"
    # logger.debug(f"in payroll_permissions_query_conditions  FULL ACCESS  user={user}")
    return ""

