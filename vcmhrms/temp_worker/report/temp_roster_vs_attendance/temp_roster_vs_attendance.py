import frappe
from frappe.utils import add_days, get_datetime, getdate, format_datetime
from vcmhrms.temp_worker.service import check_branch


def execute(filters=None):
    filters=frappe._dict(filters or {})
    if not filters.branch or not filters.from_date or not filters.to_date:
        frappe.throw('Branch and date range are required.')
    check_branch(filters.branch)
    specs=[('worker_name','Worker','Data',None),('worker','Worker ID','Link','Temp Worker'),
        ('staff_id','Staff ID','Data',None),('date','Date','Date',None),
        ('day_type','Duty / Off','Data',None),('shift','Shift','Data',None),('planned_start','Planned Start','Datetime',None),
        ('planned_end','Planned End','Datetime',None),('first_in','Inferred IN','Datetime',None),
        ('last_out','Inferred OUT','Datetime',None),('worked_hours','Hours','Float',None),
        ('required_hours','Required Hours','Float',None),('shortfall_hours','Shortfall','Float',None),
        ('late_entry','Late Entry','Check',None),('early_exit','Early Exit','Check',None),
        ('nearby_punches','Raw Punches','Data',None),
        ('planned_before_ot','Planned Before OT','Float',None),('actual_before_ot','Actual Before OT','Float',None),
        ('approved_before_ot','Approved Before OT','Float',None),
        ('planned_after_ot','Planned After OT','Float',None),('actual_after_ot','Actual After OT','Float',None),
        ('approved_after_ot','Approved After OT','Float',None),('ot_reason','OT Reason','Data',None),
        ('ot_approval_status','OT Approval','Data',None),('ot_approved_by','OT Approved By','Link','User'),
        ('punch_approval_status','Punch Approval','Data',None),('punch_approved_by','Punch Approved By','Link','User'),
        ('status','Status','Data',None),('exception','Exception','Data',None)]
    columns=[dict(fieldname=f,label=l,fieldtype=t,options=o,width=140) for f,l,t,o in specs]
    data=[]
    for item in frappe.get_list('Temp Roster',filters={'branch':filters.branch,
        'roster_date':['between',[filters.from_date,filters.to_date]]},fields=['name'],limit_page_length=0):
        roster=frappe.get_doc('Temp Roster',item.name)
        name=frappe.db.get_value('Temp Attendance',{'roster':roster.name})
        attendance=frappe.get_doc('Temp Attendance',name) if name else None
        results={r.roster_duty:r for r in attendance.results} if attendance else {}
        worker_data=frappe.db.get_value('Temp Worker',roster.worker,['worker_name','staff_id'],as_dict=True) or {}
        base=dict(worker_name=worker_data.get('worker_name') or roster.worker,worker=roster.worker,
                  staff_id=worker_data.get('staff_id'),date=roster.roster_date,day_type=roster.day_type)
        last_duty_date=max((getdate(duty.end_datetime) for duty in roster.duties),default=getdate(roster.roster_date))
        punch_end=add_days(max(last_duty_date,getdate(roster.roster_date)),1)
        raw_punches=frappe.get_all('Temp Punch',filters={'worker':roster.worker,
            'punch_time':['between',[get_datetime(roster.roster_date),get_datetime(punch_end)]]},
            pluck='punch_time',order_by='punch_time')
        nearby_punches=', '.join(format_datetime(value,'dd-MM-yyyy HH:mm') for value in raw_punches)
        if not roster.duties:
            data.append(dict(base,status='Weekly Off',exception=attendance.review_notes if attendance else ''))
        for duty in roster.duties:
            result=results.get(duty.name,frappe._dict())
            exception=result.exception or ''
            if raw_punches and not result.first_in and not result.last_out:
                exception='Punches exist outside the planned duty/OT matching window.'
            has_ot=any(float(value or 0)>0 for value in (duty.planned_before_ot_hours,
                result.actual_before_ot_hours,duty.planned_after_ot_hours,result.actual_after_ot_hours))
            data.append(dict(base,shift=duty.shift,planned_start=duty.start_datetime,planned_end=duty.end_datetime,
                planned_before_ot=duty.planned_before_ot_hours,planned_after_ot=duty.planned_after_ot_hours,
                ot_reason=duty.ot_reason,first_in=result.first_in,last_out=result.last_out,nearby_punches=nearby_punches,
                worked_hours=result.worked_hours,required_hours=result.required_hours,shortfall_hours=result.shortfall_hours,
                late_entry=result.late_entry,early_exit=result.early_exit,actual_before_ot=result.actual_before_ot_hours,
                actual_after_ot=result.actual_after_ot_hours,approved_before_ot=result.approved_before_ot_hours,
                approved_after_ot=result.approved_after_ot_hours,
                ot_approval_status=(result.ot_approval_status or 'Pending') if has_ot else '',
                ot_approved_by=result.approved_by,punch_approval_status=result.punch_approval_status,
                punch_approved_by=result.punch_approved_by,status=result.status or 'Pending',exception=exception))
    return columns,data
