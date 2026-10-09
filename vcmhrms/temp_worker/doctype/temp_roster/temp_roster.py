from frappe.model.document import Document
import frappe
from datetime import timedelta
from frappe.utils import get_datetime, getdate, add_days
from vcmhrms.temp_worker.service import check_branch, recalculate


class TempRoster(Document):
    def validate(self):
        check_branch(self.branch)
        old=self.get_doc_before_save()
        if old and (self.worker!=old.worker or getdate(self.roster_date)!=getdate(old.roster_date)):
            frappe.throw('Worker and roster date cannot change after creation; edit the duty allocations instead.')
        worker=frappe.get_doc('Temp Worker',self.worker)
        worker.check_permission('read')
        date=getdate(self.roster_date)
        if date<getdate(worker.active_from) or (worker.active_until and date>getdate(worker.active_until)):
            frappe.throw('Roster date falls outside engagement dates.')
        if frappe.db.exists('Temp Roster',{'worker':self.worker,'roster_date':self.roster_date,'name':['!=',self.name]}):
            frappe.throw('A roster already exists for this worker/date.')
        if self.day_type=='Weekly Off' and self.duties:
            frappe.throw('Weekly Off cannot contain duties. Change day type to Duty first.')
        if self.day_type=='Duty' and not self.duties:
            frappe.throw('Add at least one duty.')
        windows=[]
        for row in self.duties:
            start,end=get_datetime(row.start_datetime),get_datetime(row.end_datetime)
            if getdate(start)!=date or end<=start or (end-start).total_seconds()>86400:
                frappe.throw('Duty must start on roster date and last more than zero and at most 24 hours.')
            before_ot=row.planned_before_ot_hours or 0
            after_ot=row.planned_after_ot_hours or 0
            if before_ot<0 or after_ot<0:
                frappe.throw('Planned before/after OT cannot be negative.')
            if before_ot>12 or after_ot>12:
                frappe.throw('Planned before/after OT cannot exceed 12 hours per duty.')
            if (before_ot or after_ot) and not row.ot_reason:
                frappe.throw('Enter an OT reason when planning before-shift or after-shift OT.')
            windows.append((start-timedelta(hours=before_ot),end+timedelta(hours=after_ot)))
        neighbours=frappe.get_all('Temp Roster',filters={'worker':self.worker,
            'roster_date':['between',[add_days(date,-1),add_days(date,1)]],'name':['!=',self.name]},pluck='name')
        for name in neighbours:
            windows.extend((get_datetime(r.start_datetime)-timedelta(hours=r.planned_before_ot_hours or 0),
                            get_datetime(r.end_datetime)+timedelta(hours=r.planned_after_ot_hours or 0))
                for r in frappe.get_doc('Temp Roster',name).duties)
        windows.sort()
        if any(a[1]>b[0] for a,b in zip(windows,windows[1:])):
            frappe.throw('Duty or planned OT time overlaps another allocation.')

    def on_update(self):
        recalculate(self)
        for name in frappe.get_all('Temp Roster',filters={'worker':self.worker,
            'roster_date':['between',[add_days(self.roster_date,-1),add_days(self.roster_date,1)]],
            'name':['!=',self.name]},pluck='name'):
            recalculate(frappe.get_doc('Temp Roster',name))

