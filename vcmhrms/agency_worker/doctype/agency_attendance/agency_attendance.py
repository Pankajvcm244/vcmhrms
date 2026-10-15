from frappe.model.document import Document
import frappe
from frappe.model.naming import make_autoname
from frappe.utils import now_datetime
from vcmhrms.agency_worker.service import check_budget_location
from vcmhrms.agency_worker.permissions import require_supervisor
from vcmhrms.agency_worker.period_lock import assert_unlocked


class AgencyAttendance(Document):
    def autoname(self):
        if not self.attendance_date:
            frappe.throw('Attendance Date is required for naming.')
        month=frappe.utils.getdate(self.attendance_date).strftime('%y%m')
        self.name=make_autoname(f'AGENCYATT-{month}-.######')

    def validate(self):
        if self.flags.workforce_calculation: return
        if self.flags.attendance_exception: return
        roster=frappe.get_doc('Agency Roster',self.roster)
        assert_unlocked(roster.roster_date,roster.budget_location)
        check_budget_location(roster.budget_location)
        if self.is_new(): frappe.throw('Use Recalculate on the roster to create attendance.')
        old=self.get_doc_before_save()
        if old and any(self.get(f)!=old.get(f) for f in ['roster','worker','worker_code',
                                                          'attendance_date','late_entry','early_exit',
                                                          'worked_hours','calculated_at']):
            frappe.throw('Calculated attendance fields cannot be changed manually.')
        if old and [r.as_dict() for r in self.results]!=[r.as_dict() for r in old.results]:
            # Compare business values only; Frappe may refresh child metadata.
            fields=['roster_duty','shift','first_in','last_out','worked_hours','planned_before_ot_hours',
                    'actual_before_ot_hours','planned_after_ot_hours','actual_after_ot_hours','ot_reason',
                    'status','exception']
            if [[r.get(f) for f in fields] for r in self.results]!=[[r.get(f) for f in fields] for r in old.results]:
                frappe.throw('Duty results must be recalculated from punches.')
            correction_fields=['corrected_first_in','corrected_last_out','status_override','exception_reason',
                               'exception_by','exception_at']
            if [[r.get(f) for f in correction_fields] for r in self.results]!=[[r.get(f) for f in correction_fields] for r in old.results]:
                frappe.throw('Use the Agency Attendance Review page for corrections and rule exceptions.')
        old_approval={r.roster_duty:(r.approved_before_ot_hours or 0,r.approved_after_ot_hours or 0)
                      for r in old.results} if old else {}
        approval_changed=False
        for row in self.results:
            before=row.approved_before_ot_hours or 0
            after=row.approved_after_ot_hours or 0
            if not 0<=before<=row.actual_before_ot_hours:
                frappe.throw(f'Approved before-shift OT for {row.shift} must be between zero and actual before-shift OT.')
            if not 0<=after<=row.actual_after_ot_hours:
                frappe.throw(f'Approved after-shift OT for {row.shift} must be between zero and actual after-shift OT.')
            approval_changed |= old_approval.get(row.roster_duty,(0,0))!=(before,after)
        if approval_changed:
            require_supervisor(self.worker)
            if not self.review_notes: frappe.throw('Enter review notes when approving OT.')
            for row in self.results:
                if old_approval.get(row.roster_duty,(0,0))!=(row.approved_before_ot_hours or 0,row.approved_after_ot_hours or 0):
                    old_row=next((item for item in old.results if item.roster_duty==row.roster_duty),None)
                    if old_row and (old_row.ot_approval_status or 'Pending')!='Pending':
                        frappe.throw('Approved or rejected OT is read-only.')
                    row.ot_approval_status='Approved' if (row.approved_before_ot_hours or row.approved_after_ot_hours) else 'Rejected'
                    row.approved_by=frappe.session.user
                    row.approved_at=now_datetime()
                    row.approval_remarks=self.review_notes

