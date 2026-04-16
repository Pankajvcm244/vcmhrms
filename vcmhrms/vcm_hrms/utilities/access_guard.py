

# vcmhrms/vcm_hrms/utilities/access_guard.py

import frappe
import logging
logger = logging.getLogger(__name__)

def get_allowed_payroll_users():
    logger.debug(f"in get_allowed_payroll_users ")
    # Optional: move to System Settings / doctype later
    return [
        
        "teena.arora@vcm.org.in",
        "payroll2@yourcompany.com",
    ]

def is_payroll_user(user=None):
    user = user or frappe.session.user
    logger.debug(f"in is_payroll_user   user={user}")
    return user in get_allowed_payroll_users()

def validate_payroll_access(user=None):
    logger.debug(f"in validate_payroll_access user={user}")
    if not is_payroll_user(user):
        frappe.throw(
            "You are not authorized to access payroll data.",
            frappe.PermissionError
        )


def payroll_permissions_query_conditions(user):
    logger.debug(f"in payroll_permissions_query_conditions   user={user}")
    if not is_payroll_user(user):
        logger.debug(f"in payroll_permissions_query_conditions  NO ACCESS  user={user}")
        return "1=0"
    logger.debug(f"in payroll_permissions_query_conditions  FULL ACCESS  user={user}")
    return ""

#  for Salary Slip
def has_permission(doc, ptype, user):
    logger.debug(f"in has_permission doc={doc.name if doc else None} user={user}")
    validate_payroll_access(user)
    return True