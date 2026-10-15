from datetime import datetime

import frappe
from frappe.utils import add_months, getdate, now_datetime, today

def period_dates(month):
    try:
        end=getdate(datetime.strptime(month,'%m-%Y').date().replace(day=25))
    except (TypeError,ValueError):
        frappe.throw('Enter Month as MM-YYYY, for example 10-2026.')
    previous=getdate(add_months(end,-1))
    return previous.replace(day=26),end


def is_locked(date,budget_location):
    return bool(frappe.db.exists('Agency Attendance Lock',{
        'budget_location':budget_location,'period_start':['<=',getdate(date)],'period_end':['>=',getdate(date)]}))


def assert_unlocked(date,budget_location):
    if is_locked(date,budget_location):
        frappe.throw('This attendance period is locked. Roster, attendance, OT and punch reviews cannot be changed.')


@frappe.whitelist(methods=['POST'])
def lock_period(month,budget_location):
    from vcmhrms.agency_worker.service import check_budget_location
    if frappe.session.user=='Guest' or not ({'System Manager','Agency Worker Manager'} & set(frappe.get_roles())):
        frappe.throw('Agency Worker Manager or System Manager role required.',frappe.PermissionError)
    check_budget_location(budget_location)
    start,end=period_dates(month)
    if end>=getdate(today()):
        frappe.throw('Only a completed attendance period ending before today can be locked.')
    if is_locked(end,budget_location): return {'already_locked':1}
    pending=frappe.db.sql('''select count(*) from `tabAgency Attendance Duty` d
        join `tabAgency Attendance` a on a.name=d.parent
        join `tabAgency Roster` r on r.name=a.roster
        where r.budget_location=%s and r.roster_date between %s and %s and
        (d.status in ('Pending','Needs Review') or d.ot_approval_status='Pending' or d.punch_approval_status='Pending')''',
        (budget_location,start,end))[0][0]
    if pending: frappe.throw(f'Cannot lock this period: {pending} duty records still need supervisor action.')
    doc=frappe.get_doc({'doctype':'Agency Attendance Lock','period_label':month,'period_start':start,
        'period_end':end,'budget_location':budget_location,'locked_by':frappe.session.user,'locked_at':now_datetime()})
    doc.insert(ignore_permissions=True)
    return {'name':doc.name,'period_start':start,'period_end':end}

