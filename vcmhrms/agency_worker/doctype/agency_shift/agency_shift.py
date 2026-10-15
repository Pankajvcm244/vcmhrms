from frappe.model.document import Document
import frappe


class AgencyShift(Document):
    def validate(self):
        if self.required_hours<=0 or not 0<=self.half_day_hours<=self.required_hours:
            frappe.throw('Required hours must be positive; half-day hours must be between zero and required hours.')
        if any((getattr(self,f) or 0)<0 for f in ['break_minutes','early_minutes','late_minutes']):
            frappe.throw('Break and punch windows cannot be negative.')
        if (self.early_minutes or 0)>180 or (self.late_minutes or 0)>180:
            frappe.throw('Punch windows cannot exceed 180 minutes.')
        if not 0<self.max_before_ot_capture_hours<=12 or not 0<self.max_after_ot_capture_hours<=12:
            frappe.throw('Maximum before/after OT capture hours must be greater than zero and at most 12.')
        if not 0<=self.late_entry_grace_minutes<=180 or not 0<=self.early_exit_grace_minutes<=180:
            frappe.throw('Late-entry and early-exit grace must be between zero and 180 minutes.')

