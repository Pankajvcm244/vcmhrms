# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, get_datetime, getdate, format_datetime
from vcmhrms.agency_worker.service import check_budget_location


def execute(filters=None):
    filters=frappe._dict(filters or {})
    if not filters.budget_location or not filters.from_date or not filters.to_date:
        frappe.throw('Budget Location and date range are required.')
    check_budget_location(filters.budget_location)
    specs=[('worker_name','Agency Staff Name','Data',None),
        ('staff_id','Agency Staff ID','Data',None),('agency','Agency','Link','Agency'),('date','Date','Date',None),
        ('day_type','Duty / Off','Data',None),('shift','Shift','Data',None),('planned_start','Planned Start','Data',None),
        ('planned_end','Planned End','Data',None),('first_in','Inferred IN','Data',None),
        ('last_out','Inferred OUT','Data',None),('worked_hours','Hours','Float',None),
        ('required_hours','Required Hours','Float',None),('shortfall_hours','Shortfall','Float',None),
        ('late_entry','Late Entry','Check',None),('early_exit','Early Exit','Check',None),
        ('nearby_punches','Raw Punches','Data',None),
        ('planned_before_ot','Planned OT Before Shift','Float',None),('actual_before_ot','OT Before Shift','Float',None),
        ('approved_before_ot','Approved OT Before Shift','Float',None),
        ('planned_after_ot','Planned OT After Shift','Float',None),('actual_after_ot','OT After Shift','Float',None),
        ('approved_after_ot','Approved OT After Shift','Float',None),('ot_reason','OT Reason','Data',None),
        ('ot_approval_status','OT Approval','Data',None),('ot_approved_by','OT Approved By','Link','User'),
        ('punch_approval_status','Punch Approval','Data',None),('punch_approved_by','Punch Approved By','Link','User'),
        ('status','Status','Data',None),('exception','Exception','Data',None)]
    columns=[dict(fieldname=f,label=l,fieldtype=t,options=o,width=140) for f,l,t,o in specs]
    data=[]
    roster_filters={'budget_location':filters.budget_location,
        'roster_date':['between',[filters.from_date,filters.to_date]]}
    worker_filters={}
    if filters.worker: worker_filters['name']=filters.worker
    if filters.agency: worker_filters['agency']=filters.agency
    if filters.department: worker_filters['department']=filters.department
    if worker_filters:
        agency_workers=frappe.get_all('Agency Worker',filters=worker_filters,pluck='name')
        if not agency_workers: return columns,[]
        roster_filters['worker']=['in',agency_workers]
    if filters.day_type: roster_filters['day_type']=filters.day_type
    for item in frappe.get_list('Agency Roster',filters=roster_filters,fields=['name'],limit_page_length=0):
        roster=frappe.get_doc('Agency Roster',item.name)
        name=frappe.db.get_value('Agency Attendance',{'roster':roster.name})
        attendance=frappe.get_doc('Agency Attendance',name) if name else None
        results={r.roster_duty:r for r in attendance.results} if attendance else {}
        worker_data=frappe.db.get_value('Agency Worker',roster.worker,['worker_name','staff_id','agency'],as_dict=True) or {}
        base=dict(worker_name=worker_data.get('worker_name') or roster.worker,worker=roster.worker,
                  staff_id=worker_data.get('staff_id'),agency=worker_data.get('agency'),date=roster.roster_date,day_type=roster.day_type)
        last_duty_date=max((getdate(duty.end_datetime) for duty in roster.duties),default=getdate(roster.roster_date))
        punch_end=add_days(max(last_duty_date,getdate(roster.roster_date)),1)
        raw_punches=frappe.get_all('Agency Punch',filters={'worker':roster.worker,
            'punch_time':['between',[get_datetime(roster.roster_date),get_datetime(punch_end)]]},
            pluck='punch_time',order_by='punch_time')
        nearby_punches=', '.join(format_datetime(value,'dd-MM-yyyy HH:mm') for value in raw_punches)
        if not roster.duties:
            if not filters.shift and (not filters.status or filters.status=='Weekly Off'):
                data.append(dict(base,status='Weekly Off',exception=attendance.review_notes if attendance else ''))
        for duty in roster.duties:
            if filters.shift and duty.shift!=filters.shift: continue
            result=results.get(duty.name,frappe._dict())
            result_status=result.status or 'Pending'
            if filters.status and filters.status!=result_status: continue
            exception=result.exception or ''
            if raw_punches and not result.first_in and not result.last_out:
                exception='Punches exist outside the planned duty/OT matching window.'
            has_ot=any(float(value or 0)>0 for value in (duty.planned_before_ot_hours,
                result.actual_before_ot_hours,duty.planned_after_ot_hours,result.actual_after_ot_hours))
            data.append(dict(base,shift=duty.shift,planned_start=format_datetime(duty.start_datetime,'HH:mm'),planned_end=format_datetime(duty.end_datetime,'HH:mm'),
                planned_before_ot=duty.planned_before_ot_hours,planned_after_ot=duty.planned_after_ot_hours,
                ot_reason=duty.ot_reason,first_in=format_datetime(result.first_in,'HH:mm') if result.first_in else '',last_out=format_datetime(result.last_out,'HH:mm') if result.last_out else '',nearby_punches=nearby_punches,
                worked_hours=result.worked_hours,required_hours=result.required_hours,shortfall_hours=result.shortfall_hours,
                late_entry=result.late_entry,early_exit=result.early_exit,actual_before_ot=result.actual_before_ot_hours,
                actual_after_ot=result.actual_after_ot_hours,approved_before_ot=result.approved_before_ot_hours,
                approved_after_ot=result.approved_after_ot_hours,
                ot_approval_status=(result.ot_approval_status or 'Pending') if has_ot else '',
                ot_approved_by=result.approved_by,punch_approval_status=result.punch_approval_status,
                punch_approved_by=result.punch_approved_by,status=result.status or 'Pending',exception=exception))
    return columns,data

