# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import re
from datetime import date

import frappe

from vcmhrms.agency_worker.duration import hours_to_hhmm
from vcmhrms.agency_worker.service import check_budget_location


DEFAULT_MOBILE_ROLES={'VCM Pay Admin','VCM Pay Manager','ERP Manager','System Manager'}


def _mobile_settings():
    if frappe.db.exists('DocType','Agency Staffing Settings'):
        settings=frappe.get_single('Agency Staffing Settings')
        roles={row.role for row in settings.allowed_roles if row.role} or DEFAULT_MOBILE_ROLES
        return frappe._dict(enabled=frappe.utils.cint(settings.enabled),
            budget_location=settings.budget_location or 'VRN',roles=roles)
    return frappe._dict(enabled=1,budget_location='VRN',roles=DEFAULT_MOBILE_ROLES)


def _has_mobile_access(settings=None):
    settings=settings or _mobile_settings()
    return bool(settings.enabled and frappe.session.user!='Guest'
        and settings.roles & set(frappe.get_roles()))


@frappe.whitelist()
def get_mobile_settings():
    """Small bootstrap payload used by the mobile app to render server-controlled navigation."""
    settings=_mobile_settings()
    allowed=_has_mobile_access(settings)
    return {'enabled':bool(settings.enabled),'has_access':allowed,
        'budget_location':settings.budget_location if allowed else None}


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
def get_dashboard(payroll_cycle,budget_location=None,agency=None,department=None,worker=None):
    """Permission-checked mobile payload for the Agency Staffing dashboard."""
    settings=_mobile_settings()
    if not _has_mobile_access(settings):
        frappe.throw('VCM Pay or ERP management access is required.',frappe.PermissionError)
    budget_location=budget_location or settings.budget_location
    start,end=_period(payroll_cycle)
    from vcmhrms.agency_worker.report.agency_monthly_attendance.agency_monthly_attendance import execute as monthly_execute
    _,data,message=monthly_execute(frappe._dict(month=f'{end.month:02d}',year=str(end.year),
        budget_location=budget_location,agency=agency,department=department,worker=worker))
    worker_names=[row.name for row in data]
    reviews={}
    if worker_names:
        review_rows=frappe.db.sql("""
            select a.worker,
              count(distinct case when d.punch_approval_status='Approved'
                or d.corrected_first_in is not null or d.corrected_last_out is not null
                or coalesce(d.status_override,'')!='' then a.attendance_date end) modified_attendance,
              count(distinct case when d.punch_approval_status='Pending'
                and d.status in ('Half Day','Absent','Needs Review') then a.attendance_date end) pending_reviews
            from `tabAgency Attendance` a
            join `tabAgency Attendance Duty` d on d.parent=a.name
            where a.worker in %(workers)s and a.attendance_date between %(start)s and %(end)s
            group by a.worker
        """,{'workers':worker_names,'start':start,'end':end},as_dict=True)
        reviews={row.worker:row for row in review_rows}
    for row in data:
        review=reviews.get(row.name) or {}
        row.modified_attendance=int(review.get('modified_attendance') or 0)
        row.pending_reviews=int(review.get('pending_reviews') or 0)
        # Compatibility names used by the original web/mobile dashboard contract.
        row.approved_nr_reviews=row.modified_attendance
        row.pending_nr_reviews=row.pending_reviews
        row.approved_ot_duration=row.approved_ot
    workers=frappe.get_list('Agency Worker',filters={'budget_location':budget_location},
        fields=['name','worker_name','agency','department'],order_by='worker_name',limit_page_length=0)
    cycle_end=date.today()
    cycle_year=cycle_end.year
    cycle_month=cycle_end.month+(1 if cycle_end.day>=26 else 0)
    if cycle_month==13:
        cycle_month=1; cycle_year+=1
    cycles=[]
    for offset in range(3):
        month=cycle_month-offset; year=cycle_year
        while month<=0:
            month+=12; year-=1
        cycle_start,cycle_finish=_period(f'{year:04d}-{month:02d}')
        cycles.append({'value':f'{year:04d}-{month:02d}',
            'label':f'{cycle_finish:%B %Y} ({cycle_start:%d-%m-%Y} to {cycle_finish:%d-%m-%Y})'})
    total_ot_minutes=0
    for row in data:
        hours,minutes=(str(row.approved_ot or '00:00').split(':')+['0'])[:2]
        total_ot_minutes+=int(hours or 0)*60+int(minutes or 0)
    summary=[
        {'label':'Present','value':sum(int(row.present_days or 0) for row in data)},
        {'label':'Half Day','value':sum(int(row.half_days or 0) for row in data)},
        {'label':'Absent','value':sum(int(row.absent_days or 0) for row in data)},
        {'label':'OFF','value':sum(int(row.off_days or 0) for row in data)},
        {'label':'Modified','value':sum(row.modified_attendance for row in data)},
        {'label':'Pending Review','value':sum(row.pending_reviews for row in data)},
        {'label':'Approved OT','value':f'{total_ot_minutes//60:02d}:{total_ot_minutes%60:02d}'},
    ]
    return {
        'data':data,
        'message':message,
        'summary':summary,
        'payroll_cycles':cycles,
        'options':{
            'agencies':sorted({row.agency for row in workers if row.agency}),
            'departments':sorted({row.department for row in workers if row.department}),
            'workers':[{'value':row.name,'label':row.worker_name or row.name,
                'agency':row.agency,'department':row.department} for row in workers],
        },
    }
