import json
import frappe
from frappe.utils import getdate, now_datetime
from vcmhrms.agency_worker.service import check_budget_location
from vcmhrms.agency_worker.permissions import is_supervisor, require_supervisor
from vcmhrms.agency_worker.period_lock import assert_unlocked


def _is_system_manager():
    return frappe.session.user=='Administrator' or 'System Manager' in frappe.get_roles()


def _check_manager(worker):
    require_supervisor(worker)


@frappe.whitelist()
def get_pending_ot(from_date,to_date,budget_location=None,status='Pending'):
    if frappe.session.user=='Guest' or not frappe.has_permission('Agency Attendance',ptype='read'):
        frappe.throw('Read permission for Agency Attendance is required.',frappe.PermissionError)
    start,end=getdate(from_date),getdate(to_date)
    if end<start or (end-start).days>92:
        frappe.throw('Choose a date range of at most 93 days.')
    if budget_location: check_budget_location(budget_location)
    filters={'roster_date':['between',[start,end]]}
    if budget_location: filters['budget_location']=budget_location
    rosters=frappe.get_list('Agency Roster',filters=filters,fields=['name','worker','roster_date','budget_location'],limit_page_length=0)
    rows=[]
    worker_cache={}
    for roster in rosters:
        if roster.worker not in worker_cache:
            worker=frappe.db.get_value('Agency Worker',roster.worker,
                ['worker_name','staff_id'],as_dict=True) or frappe._dict()
            supervisors=frappe.get_all('Agency Worker Supervisor',filters={'parent':roster.worker},pluck='user')
            worker['supervisor']=frappe.session.user if frappe.session.user in supervisors else (supervisors[0] if supervisors else None)
            worker['supervisor_name']=', '.join(supervisors)
            worker_cache[roster.worker]=worker
        worker=worker_cache[roster.worker]
        if not is_supervisor(roster.worker):
            continue
        attendance_name=frappe.db.get_value('Agency Attendance',{'roster':roster.name})
        if not attendance_name: continue
        attendance=frappe.get_doc('Agency Attendance',attendance_name)
        roster_doc=frappe.get_doc('Agency Roster',roster.name)
        roster_duties={duty.name:duty for duty in roster_doc.duties}
        worker_name=worker.worker_name or roster.worker
        for result in attendance.results:
            roster_duty=roster_duties.get(result.roster_duty)
            if not roster_duty: continue
            if not any((result.planned_before_ot_hours,result.actual_before_ot_hours,
                        result.planned_after_ot_hours,result.actual_after_ot_hours)):
                continue
            approval_status=result.ot_approval_status or 'Pending'
            if status and status!='All' and approval_status!=status: continue
            rows.append(dict(attendance=attendance.name,roster_duty=result.roster_duty,worker=roster.worker,
                worker_name=worker_name,date=roster.roster_date,budget_location=roster.budget_location,shift=result.shift,
                staff_id=worker.staff_id,supervisor=worker.supervisor,
                supervisor_name=worker.supervisor_name,
                planned_before=roster_duty.planned_before_ot_hours or 0,actual_before=result.actual_before_ot_hours or 0,
                approved_before=result.approved_before_ot_hours or 0,planned_after=roster_duty.planned_after_ot_hours or 0,
                actual_after=result.actual_after_ot_hours or 0,approved_after=result.approved_after_ot_hours or 0,
                reason=roster_duty.ot_reason,status=approval_status,approved_by=result.approved_by,
                approved_at=result.approved_at,remarks=result.approval_remarks))
    return rows


@frappe.whitelist(methods=['POST'])
def approve_ot(rows,remarks,decision='Custom'):
    if frappe.session.user=='Guest' or not frappe.has_permission('Agency Attendance',ptype='read'):
        frappe.throw('Read permission for Agency Attendance is required.',frappe.PermissionError)
    if isinstance(rows,str): rows=json.loads(rows)
    if not isinstance(rows,list) or not 1<=len(rows)<=500: frappe.throw('Select between 1 and 500 OT rows.')
    remarks=(remarks or '').strip()
    if not remarks: frappe.throw('Approval remarks are required.')
    if decision not in ('Custom','Planned','Actual','Reject'): frappe.throw('Invalid approval decision.')
    grouped={}
    for request in rows:
        grouped.setdefault(request.get('attendance'),[]).append(request)
    updated=0
    for attendance_name,requests in grouped.items():
        frappe.db.sql('select name from `tabAgency Attendance` where name=%s for update',attendance_name)
        attendance=frappe.get_doc('Agency Attendance',attendance_name)
        attendance.check_permission('write')
        _check_manager(attendance.worker)
        roster=frappe.get_doc('Agency Roster',attendance.roster)
        assert_unlocked(roster.roster_date,roster.budget_location)
        check_budget_location(roster.budget_location)
        by_duty={row.roster_duty:row for row in attendance.results}
        roster_duties={row.name:row for row in roster.duties}
        for request in requests:
            row=by_duty.get(request.get('roster_duty'))
            if not row: frappe.throw('Attendance duty no longer exists; reload the approval page.')
            if not any((row.planned_before_ot_hours,row.actual_before_ot_hours,
                        row.planned_after_ot_hours,row.actual_after_ot_hours)):
                frappe.throw('This duty has no OT to approve.')
            if (row.ot_approval_status or 'Pending')!='Pending':
                frappe.throw('This OT was already approved or rejected and is read-only.')
            roster_duty=roster_duties.get(row.roster_duty)
            if not roster_duty: frappe.throw('Roster duty no longer exists; reload the approval page.')
            if decision=='Planned':
                before=min(roster_duty.planned_before_ot_hours or 0,row.actual_before_ot_hours or 0)
                after=min(roster_duty.planned_after_ot_hours or 0,row.actual_after_ot_hours or 0)
            elif decision=='Actual':
                before,after=row.actual_before_ot_hours or 0,row.actual_after_ot_hours or 0
            elif decision=='Reject':
                before=after=0
            else:
                before=float(request.get('approved_before') or 0)
                after=float(request.get('approved_after') or 0)
            actual_before=row.actual_before_ot_hours or 0
            actual_after=row.actual_after_ot_hours or 0
            if not 0<=before<=actual_before or not 0<=after<=actual_after:
                frappe.throw(f'Approval for {attendance.worker} {row.shift} exceeds actual OT.')
            row.approved_before_ot_hours=before
            row.approved_after_ot_hours=after
            row.ot_approval_status='Rejected' if decision=='Reject' else 'Approved'
            row.approved_by=frappe.session.user
            row.approved_at=now_datetime()
            row.approval_remarks=remarks
            updated+=1
        attendance.review_notes=remarks
        attendance.flags.bulk_ot_approval=True
        attendance.save()
    return dict(updated=updated)

