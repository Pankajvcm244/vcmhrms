# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

from collections import defaultdict
from datetime import timedelta

import frappe
from frappe.utils import getdate
from frappe.utils import today

from vcmhrms.agency_worker.period_lock import period_dates
from vcmhrms.agency_worker.permissions import is_supervisor
from vcmhrms.agency_worker.service import check_budget_location




def _day_status(items):
    statuses=[item.status or 'Pending' for item in items]
    if any(s=='Needs Review' for s in statuses): return 'NR'
    if any(s=='Pending' for s in statuses): return 'PD'
    if all(s=='Present' for s in statuses): return 'P'
    if all(s=='Absent' for s in statuses): return 'A'
    return 'HD'


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_supervisors(doctype,txt,searchfield,start,page_len,filters):
    values={'txt':f'%{txt}%','start':int(start or 0),'page_len':min(int(page_len or 20),50)}
    return frappe.db.sql("""select distinct u.name,u.full_name from `tabAgency Worker Supervisor` s
        join `tabUser` u on u.name=s.user where u.enabled=1 and
        (u.name like %(txt)s or coalesce(u.full_name,'') like %(txt)s)
        order by u.full_name limit %(start)s,%(page_len)s""",values)


@frappe.whitelist()
def get_payroll_years():
    current_year=getdate(today()).year
    years={current_year}
    years.update(int(row.year) for row in frappe.db.sql(
        """select distinct year(roster_date) year from `tabAgency Roster`
        where roster_date is not null order by year desc""",as_dict=True) if row.year)
    return [str(year) for year in sorted(years,reverse=True)]


def execute(filters=None):
    filters=frappe._dict(filters or {})
    if not filters.month: frappe.throw('Month is required.')
    if filters.year:
        try: period=f'{int(str(filters.month)[:2]):02d}-{int(filters.year):04d}'
        except (TypeError,ValueError): frappe.throw('Select a valid payroll month and year.')
    else:
        period=filters.month
    start,end=period_dates(period)
    if filters.budget_location: check_budget_location(filters.budget_location)
    conditions=['w.status=%(status)s']; values={'status':'Active'}
    if filters.budget_location: conditions.append('w.budget_location=%(location)s'); values['location']=filters.budget_location
    if filters.agency: conditions.append('w.agency=%(agency)s'); values['agency']=filters.agency
    if filters.department: conditions.append('w.department=%(department)s'); values['department']=filters.department
    if filters.worker: conditions.append('w.name=%(worker)s'); values['worker']=filters.worker
    supervisor=filters.supervisor
    if supervisor:
        conditions.append("""exists(select 1 from `tabAgency Worker Supervisor` s
            where s.parent=w.name and s.parenttype='Agency Worker' and s.user=%(supervisor)s)""")
        values['supervisor']=supervisor
    workers=frappe.db.sql(f"""select w.name,w.worker_name,w.staff_id,w.agency,w.department,w.budget_location
        from `tabAgency Worker` w where {' and '.join(conditions)} order by w.worker_name""",values,as_dict=True)
    dates=[]; current=start
    while current<=end: dates.append(current); current+=timedelta(days=1)
    columns=[
        dict(fieldname='worker_name',label='Agency Staff Name',fieldtype='Data',width=180),
        dict(fieldname='staff_id',label='Agency Staff ID',fieldtype='Data',width=110),
        dict(fieldname='agency',label='Agency',fieldtype='Link',options='Agency',width=130),
        dict(fieldname='department',label='Department',fieldtype='Data',width=130),
        dict(fieldname='budget_location',label='Budget Location',fieldtype='Link',options='Budget Location',width=110)]
    columns += [dict(fieldname=f'day_{i}',label=date.strftime('%d %a'),fieldtype='Data',width=92) for i,date in enumerate(dates,1)]
    columns += [dict(fieldname='present_days',label='Present',fieldtype='Int',width=75),
        dict(fieldname='half_days',label='Half Days',fieldtype='Int',width=80),
        dict(fieldname='absent_days',label='Absent',fieldtype='Int',width=75),
        dict(fieldname='approved_ot',label='Approved OT',fieldtype='Float',width=95)]
    if not workers: return columns,[],f'Attendance period: {start:%d-%m-%Y} to {end:%d-%m-%Y}. ✓ means no supervisor action is pending.'
    names=[w.name for w in workers]
    rosters=frappe.get_all('Agency Roster',filters={'worker':['in',names],'roster_date':['between',[start,end]]},fields=['name','worker','roster_date','day_type'])
    by_day=defaultdict(list); offs=set()
    if rosters:
        roster_names=[r.name for r in rosters]; placeholders=','.join(['%s']*len(roster_names))
        records=frappe.db.sql(f"""select r.worker,r.roster_date,d.status,d.worked_hours,d.approved_before_ot_hours+
            d.approved_after_ot_hours approved_ot,d.ot_approval_status,d.punch_approval_status,
            d.late_entry,d.early_exit from `tabAgency Roster` r join `tabAgency Attendance` a on a.roster=r.name
            join `tabAgency Attendance Duty` d on d.parent=a.name where r.name in ({placeholders})""",tuple(roster_names),as_dict=True)
        for x in records: by_day[(x.worker,getdate(x.roster_date))].append(x)
        offs={(r.worker,getdate(r.roster_date)) for r in rosters if r.day_type=='Weekly Off'}
    agency_meta={x.name:x for x in frappe.get_all('Agency',fields=['name','agency_holiday_list','holiday_colour'])}
    holiday_lists={meta.agency_holiday_list for meta in agency_meta.values()
        if meta.agency_holiday_list}
    enabled_holiday_lists=set(frappe.get_all('Agency Holiday List',
        filters={'name':['in',list(holiday_lists)],'disabled':0},pluck='name')) if holiday_lists else set()
    holidays=defaultdict(set)
    for name,meta in agency_meta.items():
        if meta.agency_holiday_list in enabled_holiday_lists:
            holidays[name]=set(getdate(x.holiday_date) for x in frappe.get_all(
                'Agency Holiday',filters={'parent':meta.agency_holiday_list,
                    'parenttype':'Agency Holiday List','holiday_date':['between',[start,end]]},
                fields=['holiday_date']))
    data=[]
    for worker in workers:
        row=frappe._dict(**worker,present_days=0,half_days=0,absent_days=0,approved_ot=0,
            holiday_colour=(agency_meta.get(worker.agency) or {}).get('holiday_colour') or '#6f42c1',
            holiday_fields=[])
        for i,date in enumerate(dates,1):
            fieldname=f'day_{i}'
            items=by_day.get((worker.name,date),[])
            is_holiday=date in holidays.get(worker.agency,set())
            if is_holiday: row.holiday_fields.append(fieldname)
            if (worker.name,date) in offs: code='OFF ✓'
            elif not items:
                row[fieldname]='—'
                continue
            else:
                code=_day_status(items)
                if code=='P': row.present_days+=1
                elif code=='HD': row.half_days+=1
                elif code=='A': row.absent_days+=1
                row.approved_ot+=sum(float(x.approved_ot or 0) for x in items)
                flags=(' L' if any(x.late_entry for x in items) else '')+(' E' if any(x.early_exit for x in items) else '')
                pending=any((x.status in ('Pending','Needs Review') or x.ot_approval_status=='Pending' or x.punch_approval_status=='Pending') for x in items)
                code+=flags+('' if pending else ' ✓')
            row[fieldname]=code
        row.approved_ot=round(row.approved_ot,2); data.append(row)
    message=(f'Attendance period: {start:%d-%m-%Y} to {end:%d-%m-%Y}. '
        'Codes: P = Present; HD = Half Day; A = Absent; OFF = Weekly Off; '
        'PD = Pending (shift or punch-capture window not completed); NR = Needs Review; '
        'L = Late Entry; E = Early Exit; — = No roster. '
        '✓ = no pending punch or OT action. Agency holidays use the agency holiday colour.')
    return columns,data,message


