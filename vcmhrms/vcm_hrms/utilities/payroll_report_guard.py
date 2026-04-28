# Copyright (c) 2026, Pankaj Sharma
# For license information, please see license.txt

import frappe
from frappe.desk.query_report import run as original_query_report_run
from vcmhrms.vcm_hrms.utilities.access_guard import (
    CONFIDENTIAL_PAYROLL_REPORTS,
    validate_payroll_access,
)


@frappe.whitelist()
def run(report_name, filters=None, user=None, ignore_prepared_report=False, are_default_filters=True):
    if report_name in CONFIDENTIAL_PAYROLL_REPORTS:
        validate_payroll_access(user or frappe.session.user)

    return original_query_report_run(
        report_name=report_name,
        filters=filters,
        user=user,
        ignore_prepared_report=ignore_prepared_report,
        are_default_filters=are_default_filters,
    )