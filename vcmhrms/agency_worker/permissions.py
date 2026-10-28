# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe


def is_roster_manager(worker=None,user=None):
    user=user or frappe.session.user
    return user=='Administrator' or frappe.has_permission('Agency Roster',ptype='write',user=user)


def is_supervisor(worker=None,user=None):
    user=user or frappe.session.user
    return user=='Administrator' or frappe.has_permission('Agency Attendance',ptype='write',user=user)


def require_roster_manager(worker=None):
    if not is_roster_manager(worker):
        frappe.throw('Write permission for Agency Roster is required.',frappe.PermissionError)


def require_supervisor(worker=None):
    if not is_supervisor(worker):
        frappe.throw('Write permission for Agency Attendance is required.',frappe.PermissionError)
