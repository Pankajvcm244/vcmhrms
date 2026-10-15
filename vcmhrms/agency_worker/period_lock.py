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
    duty_rosters=frappe.db.count('Agency Roster',filters={
        'budget_location':budget_location,'roster_date':['between',[start,end]],'day_type':'Duty'})
    if not duty_rosters:
        frappe.throw('Cannot lock this period because it contains no Duty rosters.')
    incomplete=frappe.db.sql('''select count(*)
        from `tabAgency Roster` r
        left join `tabAgency Roster Duty` rd
          on rd.parent=r.name and rd.parenttype='Agency Roster'
        left join `tabAgency Attendance` a on a.roster=r.name
        left join `tabAgency Attendance Duty` ad
          on ad.parent=a.name and ad.parenttype='Agency Attendance'
         and ad.roster_duty=rd.name
        where r.budget_location=%s
          and r.roster_date between %s and %s
          and r.day_type='Duty'
          and (
              rd.name is null
              or a.name is null
              or ad.name is null
              or coalesce(ad.status,'Pending') in ('Pending','Needs Review')
              or coalesce(ad.ot_approval_status,'Not Required')='Pending'
              or coalesce(ad.punch_approval_status,'Not Required')='Pending'
          )''',(budget_location,start,end))[0][0]
    if incomplete:
        frappe.throw(
            f'Cannot lock this period: {incomplete} roster duties are missing attendance '
            'or still need supervisor action.'
        )
    doc=frappe.get_doc({'doctype':'Agency Attendance Lock','period_label':month,'period_start':start,
        'period_end':end,'budget_location':budget_location,'locked_by':frappe.session.user,'locked_at':now_datetime()})
    doc.insert(ignore_permissions=True)
    return {'name':doc.name,'period_start':start,'period_end':end}

