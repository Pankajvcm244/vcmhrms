import frappe
from frappe.utils import get_datetime, now_datetime, getdate, add_days
from vcmhrms.agency_worker.engine import calculate, evaluate_times
from vcmhrms.agency_worker.period_lock import is_locked


def check_budget_location(budget_location):
    """Compatibility hook; Agency access is controlled by roles and worker mappings."""
    return


def recalculate(roster):
    if is_locked(roster.roster_date,roster.budget_location):
        return frappe.db.get_value('Agency Attendance',{'roster':roster.name})
    # Serialize competing recalculations for the same roster, including API requests.
    frappe.db.sql('select name from `tabAgency Roster` where name=%s for update', roster.name)
    duties=[]
    for row in roster.duties:
        shift=frappe.get_doc('Agency Shift', row.shift)
        duties.append(dict(id=row.name, shift=row.shift, start=get_datetime(row.start_datetime),
            end=get_datetime(row.end_datetime), early=shift.early_minutes or 0,
            late=shift.late_minutes or 0, required_hours=shift.required_hours,
            half_day_hours=shift.half_day_hours or 0, break_minutes=shift.break_minutes or 0,
            planned_before_ot_hours=row.planned_before_ot_hours or 0,
            planned_after_ot_hours=row.planned_after_ot_hours or 0,
            ot_reason=row.ot_reason,
            max_before_ot_capture_hours=shift.max_before_ot_capture_hours or 0,
            max_after_ot_capture_hours=shift.max_after_ot_capture_hours or 0,
            late_entry_grace_minutes=shift.late_entry_grace_minutes or 0,
            early_exit_grace_minutes=shift.early_exit_grace_minutes or 0))
    low=get_datetime(add_days(roster.roster_date,-1))
    high=get_datetime(add_days(roster.roster_date, 3))
    punches=[get_datetime(p) for p in frappe.get_all('Agency Punch',
        filters={'worker':roster.worker,'punch_time':['between',[low,high]]}, pluck='punch_time')]
    # Include neighbouring rosters in matching to detect cross-day ambiguous windows.
    neighbours=frappe.get_all('Agency Roster', filters={'worker':roster.worker,
        'roster_date':['between',[add_days(roster.roster_date,-1),add_days(roster.roster_date,1)]],
        'name':['!=',roster.name]}, pluck='name')
    for name in neighbours:
        for row in frappe.get_doc('Agency Roster', name).duties:
            shift=frappe.get_doc('Agency Shift',row.shift)
            duties.append(dict(id=row.name,shift=row.shift,start=get_datetime(row.start_datetime),
                end=get_datetime(row.end_datetime),early=shift.early_minutes or 0,late=shift.late_minutes or 0,
                required_hours=shift.required_hours,half_day_hours=shift.half_day_hours or 0,
                break_minutes=shift.break_minutes or 0,
                planned_before_ot_hours=row.planned_before_ot_hours or 0,
                planned_after_ot_hours=row.planned_after_ot_hours or 0,
                ot_reason=row.ot_reason,
                max_before_ot_capture_hours=shift.max_before_ot_capture_hours or 0,
                max_after_ot_capture_hours=shift.max_after_ot_capture_hours or 0,
                late_entry_grace_minutes=shift.late_entry_grace_minutes or 0,
                early_exit_grace_minutes=shift.early_exit_grace_minutes or 0))
    results=calculate(duties,punches,now_datetime())
    name=frappe.db.get_value('Agency Attendance',{'roster':roster.name})
    doc=frappe.get_doc('Agency Attendance',name) if name else frappe.new_doc('Agency Attendance')
    doc.update(dict(roster=roster.name,worker=roster.worker,worker_code=roster.worker,
                    attendance_date=roster.roster_date,
                    calculated_at=now_datetime()))
    current_results=results[:len(roster.duties)]
    old_exceptions={r.roster_duty:dict(corrected_first_in=r.corrected_first_in,
        corrected_last_out=r.corrected_last_out,status_override=r.status_override,
        exception_reason=r.exception_reason,exception_by=r.exception_by,exception_at=r.exception_at)
        for r in doc.results}
    for index,result in enumerate(current_results):
        correction=old_exceptions.get(result['roster_duty'],{})
        if correction.get('corrected_first_in') and correction.get('corrected_last_out'):
            evaluate_times(result,duties[index],get_datetime(correction['corrected_first_in']),
                           get_datetime(correction['corrected_last_out']),True)
        if correction.get('status_override'):
            result['status']=correction['status_override']
        result.update(correction)
        has_ot=any(float(result.get(field) or 0)>0 for field in (
            'planned_before_ot_hours','actual_before_ot_hours','planned_after_ot_hours','actual_after_ot_hours'))
        needs_punch_review=(result.get('status') in ('Half Day','Absent','Needs Review') or
                            result.get('late_entry') or result.get('early_exit'))
        result['ot_approval_status']='Pending' if has_ot else 'Not Required'
        result['punch_approval_status']='Pending' if needs_punch_review else 'Not Required'
    doc.late_entry=int(any(result.get('late_entry') for result in current_results))
    doc.early_exit=int(any(result.get('early_exit') for result in current_results))
    doc.worked_hours=round(sum(float(result.get('worked_hours') or 0) for result in current_results),2)
    fields=['roster_duty','shift','first_in','last_out','worked_hours','required_hours','shortfall_hours',
            'late_entry','early_exit','planned_before_ot_hours','actual_before_ot_hours',
            'planned_after_ot_hours','actual_after_ot_hours','ot_reason','status','exception']
    old_values=[[str(r.get(f)) for f in fields] for r in doc.results]
    new_values=[[str(r.get(f)) for f in fields] for r in current_results]
    old_approvals={r.roster_duty:dict(before=r.approved_before_ot_hours or 0,after=r.approved_after_ot_hours or 0,
        status=r.ot_approval_status,approved_by=r.approved_by,approved_at=r.approved_at,
        remarks=r.approval_remarks,punch_status=r.punch_approval_status,
        punch_by=r.punch_approved_by,punch_at=r.punch_approved_at,
        punch_remarks=r.punch_approval_remarks) for r in doc.results}
    if old_values!=new_values and any(value['before'] or value['after'] for value in old_approvals.values()):
        doc.review_notes=(doc.review_notes or '')+'\nOT approval reset because duty results changed; review again.'
    doc.set('results',current_results)
    if old_values==new_values:
        for row in doc.results:
            approval=old_approvals.get(row.roster_duty,{})
            if row.ot_approval_status=='Pending':
                row.approved_before_ot_hours=approval.get('before',0)
                row.approved_after_ot_hours=approval.get('after',0)
                row.ot_approval_status=approval.get('status') or 'Pending'
                row.approved_by=approval.get('approved_by')
                row.approved_at=approval.get('approved_at')
                row.approval_remarks=approval.get('remarks')
            if row.punch_approval_status=='Pending':
                row.punch_approval_status=approval.get('punch_status') or 'Pending'
                row.punch_approved_by=approval.get('punch_by')
                row.punch_approved_at=approval.get('punch_at')
                row.punch_approval_remarks=approval.get('punch_remarks')
    off_start=get_datetime(roster.roster_date)
    off_end=get_datetime(add_days(roster.roster_date,1))
    if roster.day_type == 'Weekly Off' and any(off_start<=p<off_end for p in punches):
        # Calendar-day presence on an off is only a warning; overnight previous duty may explain it.
        if not doc.review_notes:
            doc.review_notes = 'Review punches on weekly off, including previous overnight duty.'
    doc.flags.workforce_calculation=True
    doc.save(ignore_permissions=True)
    return doc.name


@frappe.whitelist(methods=['POST'])
def recalculate_attendance(roster):
    doc=frappe.get_doc('Agency Roster',roster)
    doc.check_permission('write')
    check_budget_location(doc.budget_location)
    return recalculate(doc)

