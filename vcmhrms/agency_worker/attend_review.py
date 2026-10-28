# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, get_datetime, getdate, now_datetime
from vcmhrms.agency_worker.service import check_budget_location, recalculate
from vcmhrms.agency_worker.permissions import require_supervisor, is_supervisor
from vcmhrms.agency_worker.period_lock import assert_unlocked


def _system_manager():
    return frappe.session.user=='Administrator' or 'System Manager' in frappe.get_roles()


def _check_worker_manager(worker):
    require_supervisor(worker)


def _check_role():
    if frappe.session.user=='Guest' or not frappe.has_permission('Agency Attendance',ptype='read'):
        frappe.throw('Read permission for Agency Attendance is required.',frappe.PermissionError)


@frappe.whitelist()
def get_exceptions(from_date,to_date,budget_location=None,issue='All',review_status='Pending'):
    _check_role()
    start,end=getdate(from_date),getdate(to_date)
    if end<start or (end-start).days>92: frappe.throw('Choose a date range of at most 93 days.')
    if budget_location: check_budget_location(budget_location)
    filters={'roster_date':['between',[start,end]]}
    if budget_location: filters['budget_location']=budget_location
    rows=[]
    for roster_row in frappe.get_list('Agency Roster',filters=filters,
            fields=['name','worker','roster_date','budget_location'],limit_page_length=0):
        if not is_supervisor(roster_row.worker):
            continue
        attendance_name=frappe.db.get_value('Agency Attendance',{'roster':roster_row.name})
        if not attendance_name: continue
        attendance=frappe.get_doc('Agency Attendance',attendance_name)
        worker_name=frappe.db.get_value('Agency Worker',roster_row.worker,'worker_name') or roster_row.worker
        for result in attendance.results:
            issues=[]
            if result.status=='Needs Review': issues.append('Missing Punch')
            if result.status=='Half Day': issues.append('Half Day')
            if result.status=='Absent': issues.append('Absent')
            if result.late_entry: issues.append('Late Entry')
            if result.early_exit: issues.append('Early Exit')
            if not issues or (issue!='All' and issue not in issues): continue
            punch_status=result.punch_approval_status or 'Pending'
            if review_status!='All' and punch_status!=review_status: continue
            rows.append(dict(attendance=attendance.name,roster=roster_row.name,roster_duty=result.roster_duty,
                worker=roster_row.worker,worker_name=worker_name,date=roster_row.roster_date,budget_location=roster_row.budget_location,
                shift=result.shift,first_in=result.first_in,last_out=result.last_out,worked_hours=result.worked_hours or 0,
                required_hours=result.required_hours or 0,shortfall_hours=result.shortfall_hours or 0,
                status=result.status,late_entry=result.late_entry,early_exit=result.early_exit,issues=', '.join(issues),
                corrected_first_in=result.corrected_first_in,corrected_last_out=result.corrected_last_out,
                status_override=result.status_override,exception_reason=result.exception_reason,
                exception_by=result.exception_by,exception_at=result.exception_at,
                punch_approval_status=punch_status,
                punch_approved_by=result.punch_approved_by,punch_approved_at=result.punch_approved_at,
                punch_approval_remarks=result.punch_approval_remarks))
    return rows


@frappe.whitelist(methods=['POST'])
def apply_exception(attendance,roster_duty,reason,corrected_first_in=None,corrected_last_out=None,status_override=None):
    _check_role()
    reason=(reason or '').strip()
    if not reason: frappe.throw('Correction or exception reason is required.')
    if status_override not in (None,'','Present','Half Day','Absent'): frappe.throw('Invalid status override.')
    if bool(corrected_first_in)!=bool(corrected_last_out):
        frappe.throw('Enter both corrected first-in and corrected last-out, or leave both empty.')
    if not corrected_first_in and not status_override:
        frappe.throw('Enter corrected punches or select a status override.')
    frappe.db.sql('select name from `tabAgency Attendance` where name=%s for update',attendance)
    doc=frappe.get_doc('Agency Attendance',attendance)
    doc.check_permission('write')
    _check_worker_manager(doc.worker)
    roster=frappe.get_doc('Agency Roster',doc.roster)
    assert_unlocked(roster.roster_date,roster.budget_location)
    check_budget_location(roster.budget_location)
    result=next((row for row in doc.results if row.roster_duty==roster_duty),None)
    duty=next((row for row in roster.duties if row.name==roster_duty),None)
    if not result or not duty: frappe.throw('Attendance or roster duty changed; reload the review page.')
    if corrected_first_in:
        first,last=get_datetime(corrected_first_in),get_datetime(corrected_last_out)
        if last<=first or (last-first).total_seconds()>36*3600:
            frappe.throw('Corrected punch pair must be chronological and at most 36 hours apart.')
        if first<get_datetime(add_days(roster.roster_date,-1)) or last>get_datetime(add_days(roster.roster_date,2)):
            frappe.throw('Corrected punches are outside the permitted roster-date window.')
        result.corrected_first_in=first
        result.corrected_last_out=last
    result.status_override=status_override or None
    result.exception_reason=reason
    result.exception_by=frappe.session.user
    result.exception_at=now_datetime()
    doc.flags.attendance_exception=True
    doc.save()
    attendance_name=recalculate(roster)
    # Recalculation intentionally resets approval when calculated values change. Mark this
    # manager-entered correction reviewed only after its corrected result has been produced.
    corrected=frappe.get_doc('Agency Attendance',attendance_name)
    corrected_result=next((row for row in corrected.results if row.roster_duty==roster_duty),None)
    if not corrected_result: frappe.throw('Corrected attendance duty could not be found after recalculation.')
    corrected_result.punch_approval_status='Approved'
    corrected_result.punch_approved_by=frappe.session.user
    corrected_result.punch_approved_at=now_datetime()
    corrected_result.punch_approval_remarks=reason
    corrected.flags.attendance_exception=True
    corrected.save()
    return attendance_name


@frappe.whitelist(methods=['POST'])
def approve_punch(attendance,roster_duty,remarks):
    """Acknowledge calculated late/early/short-hours attendance without changing punches or status."""
    _check_role()
    remarks=(remarks or '').strip()
    if not remarks: frappe.throw('Punch approval remarks are required.')
    frappe.db.sql('select name from `tabAgency Attendance` where name=%s for update',attendance)
    doc=frappe.get_doc('Agency Attendance',attendance)
    doc.check_permission('write')
    _check_worker_manager(doc.worker)
    roster=frappe.get_doc('Agency Roster',doc.roster)
    assert_unlocked(roster.roster_date,roster.budget_location)
    check_budget_location(roster.budget_location)
    result=next((row for row in doc.results if row.roster_duty==roster_duty),None)
    if not result: frappe.throw('Attendance duty changed; reload the review page.')
    if (result.punch_approval_status or 'Pending')!='Pending':
        frappe.throw('This punch exception is already reviewed.')
    if not (result.late_entry or result.early_exit or result.status in ('Half Day','Absent','Needs Review')):
        frappe.throw('This attendance has no punch exception requiring approval.')
    result.punch_approval_status='Approved'
    result.punch_approved_by=frappe.session.user
    result.punch_approved_at=now_datetime()
    result.punch_approval_remarks=remarks
    doc.flags.attendance_exception=True
    doc.save()
    return dict(attendance=doc.name,approved_by=frappe.session.user)

