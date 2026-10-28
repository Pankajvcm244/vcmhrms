# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import getdate

from vcmhrms.agency_worker.service import check_budget_location


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.from_date or not filters.to_date or not filters.budget_location:
        frappe.throw('From Date, To Date and Budget Location are required.')

    start = getdate(filters.from_date)
    end = getdate(filters.to_date)
    if end < start:
        frappe.throw('To Date cannot be before From Date.')
    if (end - start).days > 366:
        frappe.throw('Choose a date range of at most 367 days.')
    if not frappe.has_permission('Agency Roster', ptype='report'):
        frappe.throw('Not permitted to report on Agency Roster.', frappe.PermissionError)
    check_budget_location(filters.budget_location)

    conditions = [
        'roster.roster_date between %(from_date)s and %(to_date)s',
        'roster.budget_location = %(budget_location)s',
        "roster.day_type = 'Duty'",
    ]
    values = {'from_date': start, 'to_date': end, 'budget_location': filters.budget_location}
    if filters.agency:
        conditions.append('worker.agency = %(agency)s')
        values['agency'] = filters.agency
    if filters.department:
        conditions.append('worker.department = %(department)s')
        values['department'] = filters.department
    if filters.shift:
        conditions.append('duty.shift = %(shift)s')
        values['shift'] = filters.shift

    data = frappe.db.sql(
        f"""
        select
            duty.shift,
            count(distinct roster.worker) as staff_count,
            count(distinct concat(roster.worker, '|', roster.roster_date)) as assigned_duty_days,
            min(roster.roster_date) as first_roster_date,
            max(roster.roster_date) as last_roster_date
        from `tabAgency Roster` roster
        inner join `tabAgency Roster Duty` duty
            on duty.parent = roster.name
            and duty.parenttype = 'Agency Roster'
            and duty.parentfield = 'duties'
        inner join `tabAgency Worker` worker on worker.name = roster.worker
        where {' and '.join(conditions)}
        group by duty.shift
        order by staff_count desc, duty.shift asc
        """,
        values,
        as_dict=True,
    )

    columns = [
        {'fieldname': 'shift', 'label': 'Shift', 'fieldtype': 'Link', 'options': 'Agency Shift', 'width': 260},
        {'fieldname': 'staff_count', 'label': 'Assigned Staff', 'fieldtype': 'Int', 'width': 130},
        {'fieldname': 'assigned_duty_days', 'label': 'Assigned Duty Days', 'fieldtype': 'Int', 'width': 160},
        {'fieldname': 'first_roster_date', 'label': 'First Roster Date', 'fieldtype': 'Date', 'width': 140},
        {'fieldname': 'last_roster_date', 'label': 'Last Roster Date', 'fieldtype': 'Date', 'width': 140},
    ]
    summary = [
        {'value': sum(row.staff_count for row in data), 'indicator': 'Blue', 'label': 'Staff Count Across Shifts', 'datatype': 'Int'},
        {'value': sum(row.assigned_duty_days for row in data), 'indicator': 'Green', 'label': 'Assigned Duty Days', 'datatype': 'Int'},
    ]
    return columns, data, None, None, summary

