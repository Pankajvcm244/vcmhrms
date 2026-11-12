# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

from frappe.model.document import Document
import frappe
from frappe.utils import flt


class AgencyShift(Document):
    def validate(self):
        required_hours = flt(self.required_hours)
        half_day_hours = flt(self.half_day_hours)
        if required_hours <= 0 or not 0 < half_day_hours < required_hours:
            frappe.throw('Required Hours must be positive. Half Day Hours must be greater than zero and less than Required Hours.')
        self.break_minutes=0
        if any((getattr(self,f) or 0)<0 for f in ['early_minutes','late_minutes']):
            frappe.throw('Punch windows cannot be negative.')
        if (self.early_minutes or 0)>180 or (self.late_minutes or 0)>180:
            frappe.throw('Punch windows cannot exceed 180 minutes.')
        if self.mode=='Fixed':
            if not self.start_time or not self.end_time:
                frappe.throw('Start Time and End Time are required for a Fixed shift.')
            if not 0<self.max_before_ot_capture_hours<=12 or not 0<self.max_after_ot_capture_hours<=12:
                frappe.throw('Maximum before/after OT capture hours must be greater than zero and at most 12.')
        else:
            self.start_time=None
            self.end_time=None
            self.max_before_ot_capture_hours=0
            self.max_after_ot_capture_hours=0
            self.early_minutes=0
            self.late_minutes=0
            self.late_entry_grace_minutes=0
            self.early_exit_grace_minutes=0
        if not 0<=self.late_entry_grace_minutes<=180 or not 0<=self.early_exit_grace_minutes<=180:
            frappe.throw('Late-entry and early-exit grace must be between zero and 180 minutes.')



