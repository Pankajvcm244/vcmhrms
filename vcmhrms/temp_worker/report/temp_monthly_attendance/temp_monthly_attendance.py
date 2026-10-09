import calendar
from collections import defaultdict
from datetime import datetime

import frappe
from frappe.utils import get_last_day, getdate

from vcmhrms.temp_worker.service import check_branch


def _system_manager():
    return frappe.session.user=='Administrator' or 'System Manager' in frappe.get_roles()


def _day_status(items):
    statuses=[item.status or 'Pending' for item in items]
    if any(status=='Needs Review' for status in statuses): return 'NR'
    if any(status=='Pending' for status in statuses): return 'PD'
    if all(status=='Present' for status in statuses): return 'P'
    if all(status=='Absent' for status in statuses): return 'A'
    return 'HD'


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_supervisors(doctype,txt,searchfield,start,page_len,filters):
    """Link search containing only users assigned as a Temp Worker Supervisor."""
    if frappe.session.user=='Guest' or not ({'System Manager','Temp Worker Manager'} & set(frappe.get_roles())):
        frappe.throw('Temp Worker Manager or System Manager role required.',frappe.PermissionError)
    filters=frappe.parse_json(filters) if isinstance(filters,str) else (filters or {})
    branch=filters.get('branch')
    if branch: check_branch(branch)
    conditions=["coalesce(w.supervisor,'')!=''",'u.enabled=1',
                '(u.name like %(txt)s or coalesce(u.full_name,\'\') like %(txt)s)']
    values={'txt':f'%{txt}%','start':int(start or 0),'page_len':min(int(page_len or 20),50)}
    if branch:
        conditions.append('w.branch=%(branch)s'); values['branch']=branch
    if not _system_manager():
        conditions.append('w.supervisor=%(current_user)s'); values['current_user']=frappe.session.user
    return frappe.db.sql(f'''select distinct u.name,u.full_name
        from `tabTemp Worker` w join `tabUser` u on u.name=w.supervisor
        where {' and '.join(conditions)} order by u.full_name,u.name
        limit %(start)s,%(page_len)s''',values)


def execute(filters=None):
    filters=frappe._dict(filters or {})
    if not filters.month: frappe.throw('Month is required.')
    if frappe.session.user=='Guest' or not ({'System Manager','Temp Worker Manager'} & set(frappe.get_roles())):
        frappe.throw('Temp Worker Manager or System Manager role required.',frappe.PermissionError)
    try:
        start=getdate(datetime.strptime(filters.month,'%m-%Y').date().replace(day=1))
    except (TypeError,ValueError):
        frappe.throw('Enter Month as MM-YYYY, for example 10-2026.')
    end=getdate(get_last_day(start))
    if filters.branch: check_branch(filters.branch)
    worker_filters={}
    if filters.branch: worker_filters['branch']=filters.branch
    if not _system_manager():
        if filters.manager and filters.manager!=frappe.session.user:
            frappe.throw('Managers can view only their own team.',frappe.PermissionError)
        worker_filters['supervisor']=frappe.session.user
    elif filters.manager:
        worker_filters['supervisor']=filters.manager
    workers=frappe.get_list('Temp Worker',filters=worker_filters,
        fields=['name','worker_name','staff_id','worker_role','department','branch','supervisor'],
        order_by='worker_name',limit_page_length=0)
    columns=[
        dict(fieldname='worker_name',label='Worker',fieldtype='Data',width=180),
        dict(fieldname='worker',label='Worker ID',fieldtype='Link',options='Temp Worker',width=110),
        dict(fieldname='staff_id',label='Staff ID',fieldtype='Data',width=110),
        dict(fieldname='worker_role',label='Role',fieldtype='Data',width=110),
        dict(fieldname='department',label='Department',fieldtype='Link',options='Department',width=130),
        dict(fieldname='branch',label='Branch',fieldtype='Link',options='Branch',width=110),
        dict(fieldname='supervisor',label='Manager',fieldtype='Link',options='User',width=160),
    ]
    days=calendar.monthrange(start.year,start.month)[1]
    for day in range(1,days+1):
        date=start.replace(day=day)
        columns.append(dict(fieldname=f'day_{day}',label=date.strftime('%d %a'),fieldtype='Data',width=92))
    columns += [
        dict(fieldname='present_days',label='Present',fieldtype='Int',width=80),
        dict(fieldname='half_days',label='Half Days',fieldtype='Int',width=85),
        dict(fieldname='absent_days',label='Absent',fieldtype='Int',width=80),
        dict(fieldname='weekly_offs',label='Weekly Offs',fieldtype='Int',width=95),
        dict(fieldname='pay_units',label='Paid Duty Units',fieldtype='Float',width=115),
        dict(fieldname='worked_hours',label='Worked Hours',fieldtype='Float',width=105),
        dict(fieldname='approved_ot',label='Approved OT',fieldtype='Float',width=100),
        dict(fieldname='pending_ot',label='Pending OT',fieldtype='Int',width=95),
        dict(fieldname='pending_punch',label='Punch Review',fieldtype='Int',width=105),
    ]
    if not workers: return columns,[]
    worker_names=[worker.name for worker in workers]
    rosters=frappe.get_list('Temp Roster',filters={'worker':['in',worker_names],
        'roster_date':['between',[start,end]]},fields=['name'],limit_page_length=0)
    by_day=defaultdict(list)
    off_days=set()
    if rosters:
        roster_names=[row.name for row in rosters]
        placeholders=', '.join(['%s']*len(roster_names))
        records=frappe.db.sql(f'''select r.worker,r.roster_date,r.day_type,rd.name roster_duty,
                coalesce(ad.status,'Pending') status,coalesce(ad.worked_hours,0) worked_hours,
                coalesce(ad.approved_before_ot_hours,0)+coalesce(ad.approved_after_ot_hours,0) approved_ot,
                coalesce(ad.planned_before_ot_hours,0)+coalesce(ad.actual_before_ot_hours,0)+
                    coalesce(ad.planned_after_ot_hours,0)+coalesce(ad.actual_after_ot_hours,0) ot_signal,
                coalesce(ad.ot_approval_status,'Not Required') ot_status,
                coalesce(ad.punch_approval_status,'Not Required') punch_status,
                coalesce(ad.late_entry,0) late_entry,coalesce(ad.early_exit,0) early_exit
            from `tabTemp Roster` r
            left join `tabTemp Roster Duty` rd on rd.parent=r.name and rd.parenttype='Temp Roster'
            left join `tabTemp Attendance` a on a.roster=r.name
            left join `tabTemp Attendance Duty` ad on ad.parent=a.name and ad.roster_duty=rd.name
            where r.name in ({placeholders})''',tuple(roster_names),as_dict=True)
        for record in records:
            key=(record.worker,getdate(record.roster_date))
            if record.day_type=='Weekly Off' and not record.roster_duty: off_days.add(key)
            elif record.roster_duty: by_day[key].append(record)
    data=[]
    for worker in workers:
        row=frappe._dict(worker_name=worker.worker_name,worker=worker.name,staff_id=worker.staff_id,
            worker_role=worker.worker_role,department=worker.department,
            branch=worker.branch,supervisor=worker.supervisor,
            present_days=0,half_days=0,absent_days=0,weekly_offs=0,pay_units=0,
            worked_hours=0,approved_ot=0,pending_ot=0,pending_punch=0)
        for day in range(1,days+1):
            date=start.replace(day=day); key=(worker.name,date); items=by_day.get(key,[])
            if key in off_days:
                row[f'day_{day}']='OFF'; row.weekly_offs+=1; continue
            if not items: row[f'day_{day}']='—'; continue
            code=_day_status(items)
            if code=='P': row.present_days+=1; row.pay_units+=1
            elif code=='HD': row.half_days+=1; row.pay_units+=0.5
            elif code=='A': row.absent_days+=1
            row.worked_hours+=sum(float(item.worked_hours or 0) for item in items)
            approved_ot=sum(float(item.approved_ot or 0) for item in items)
            row.approved_ot+=approved_ot
            pending_ot=sum(1 for item in items if item.ot_signal and item.ot_status=='Pending')
            pending_punch=sum(1 for item in items if item.punch_status=='Pending')
            row.pending_ot+=pending_ot; row.pending_punch+=pending_punch
            flags=(' L' if any(item.late_entry for item in items) else '')+(' E' if any(item.early_exit for item in items) else '')
            marker=' !' if pending_punch else ''
            ot=f' OT {approved_ot:g}' if approved_ot else (' OT?' if pending_ot else '')
            row[f'day_{day}']=f'{code}{flags}{marker}{ot}'
        for field in ('pay_units','worked_hours','approved_ot'): row[field]=round(row[field],2)
        data.append(row)
    message=('Legend: P Present, HD Half Day, A Absent, OFF Weekly Off, PD Pending, NR Needs Review, '
             'L Late, E Early, ! punch approval pending, OT? OT approval pending. '
             'Paid Duty Units = Present + 0.5 × Half Day; weekly offs are shown separately.')
    return columns,data,message
