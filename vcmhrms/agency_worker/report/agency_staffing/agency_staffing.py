# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import re
from datetime import date

import frappe

from vcmhrms.agency_worker.duration import hours_to_hhmm
from vcmhrms.agency_worker.service import check_budget_location


def _period(value):
    match=re.fullmatch(r'(\d{4})-(\d{2})',value or '')
    if not match:
        frappe.throw('Select a valid payroll cycle.')
    year,month=map(int,match.groups())
    if not 1<=month<=12:
        frappe.throw('Select a valid payroll cycle.')
    end=date(year,month,25)
    start=date(year-1,12,26) if month==1 else date(year,month-1,26)
    return start,end


def execute(filters=None):
    filters=frappe._dict(filters or {})
    if not frappe.has_permission('Agency Attendance',ptype='report'):
        frappe.throw('Report permission for Agency Attendance is required.',frappe.PermissionError)
    if not filters.payroll_cycle or not filters.budget_location:
        frappe.throw('Payroll Cycle and Budget Location are required.')
    start,end=_period(filters.payroll_cycle)
    check_budget_location(filters.budget_location)
    conditions=['w.budget_location=%(budget_location)s']
    values={'start':start,'end':end,'budget_location':filters.budget_location}
    if filters.agency:
        conditions.append('w.agency=%(agency)s'); values['agency']=filters.agency
    if filters.department:
        conditions.append('w.department=%(department)s'); values['department']=filters.department
    if filters.worker:
        conditions.append('w.name=%(worker)s'); values['worker']=filters.worker
    data=frappe.db.sql(f"""
        select w.name worker,w.worker_name,w.staff_id,w.agency,w.department,
          count(distinct case when d.punch_approval_status='Approved'
            and d.status in ('Half Day','Absent','Needs Review') then d.name end) approved_nr_reviews,
          count(distinct case when d.punch_approval_status='Pending'
            and d.status in ('Half Day','Absent','Needs Review') then d.name end) pending_nr_reviews,
          coalesce(sum(case when d.ot_approval_status='Approved'
            then coalesce(d.approved_before_ot_hours,0)+coalesce(d.approved_after_ot_hours,0)
            else 0 end),0) approved_ot_hours
        from `tabAgency Worker` w
        left join `tabAgency Attendance` a on a.worker=w.name
          and a.attendance_date between %(start)s and %(end)s
        left join `tabAgency Attendance Duty` d on d.parent=a.name
        where {' and '.join(conditions)}
          and w.active_from<=%(end)s
          and (w.active_until is null or w.active_until>=%(start)s)
        group by w.name,w.worker_name,w.staff_id,w.agency,w.department
        order by w.agency,w.worker_name
    """,values,as_dict=True)
    for row in data:
        row.approved_ot_duration=hours_to_hhmm(row.approved_ot_hours)
    columns=[
        {'fieldname':'worker_name','label':'Agency Employee Name','fieldtype':'Data','width':210},
        {'fieldname':'staff_id','label':'Agency Staff ID','fieldtype':'Data','width':130},
        {'fieldname':'agency','label':'Agency','fieldtype':'Link','options':'Agency','width':170},
        {'fieldname':'department','label':'Department','fieldtype':'Data','width':210},
        {'fieldname':'approved_nr_reviews','label':'Approved Attendance Reviews','fieldtype':'Int','width':190},
        {'fieldname':'pending_nr_reviews','label':'Pending Attendance Reviews','fieldtype':'Int','width':190},
        {'fieldname':'approved_ot_duration','label':'Approved OT','fieldtype':'Data','width':120},
    ]
    total_ot=sum(float(row.approved_ot_hours or 0) for row in data)
    summary=[
        {'value':len(data),'indicator':'Blue','label':'Agency Employees','datatype':'Int'},
        {'value':sum(row.approved_nr_reviews for row in data),'indicator':'Green','label':'Approved Reviews','datatype':'Int'},
        {'value':sum(row.pending_nr_reviews for row in data),'indicator':'Orange','label':'Pending Reviews','datatype':'Int'},
        {'value':hours_to_hhmm(total_ot),'indicator':'Green','label':'Approved OT','datatype':'Data'},
    ]
    message=f'Payroll cycle: {start:%d-%m-%Y} to {end:%d-%m-%Y}. Approved OT is displayed as HH:MM.'
    return columns,data,message,None,summary


@frappe.whitelist()
def get_dashboard(payroll_cycle,budget_location='VRN',agency=None,department=None,worker=None):
    """Permission-checked mobile payload for the Agency Staffing dashboard."""
    filters=frappe._dict(payroll_cycle=payroll_cycle,budget_location=budget_location,
        agency=agency,department=department,worker=worker)
    columns,data,message,chart,summary=execute(filters)
    workers=frappe.get_list('Agency Worker',filters={'budget_location':budget_location},
        fields=['name','worker_name','agency','department'],order_by='worker_name',limit_page_length=0)
    return {
        'data':data,
        'message':message,
        'summary':summary,
        'options':{
            'agencies':sorted({row.agency for row in workers if row.agency}),
            'departments':sorted({row.department for row in workers if row.department}),
            'workers':[{'value':row.name,'label':row.worker_name or row.name,
                'agency':row.agency,'department':row.department} for row in workers],
        },
    }
