# Copyright (c) 2026, Aman Soni and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, getdate, today, add_days, add_to_date, now
from datetime import datetime
from frappe.utils import get_datetime, add_days, add_to_date, get_time


class SpecialConcession(Document):

    # ================= VALIDATE =================

    def validate(self):
        self.validate_date_lock()
        self.validate_shift_time_range()

        if not self.change_in_time:
            self.applied_in_time = None

        if not self.change_out_time:
            self.applied_out_time = None

        if not self.change_in_time and not self.change_out_time:
            frappe.throw("You must tick at least one: Change In Time or Change Out Time.")

        if self.attendance_status == "Present":
            frappe.throw("Attendance is already marked as Present for this date.")

    # ================= ON SUBMIT =================

    def on_submit(self):

        # Only assigned approver can submit
        if self.approver != frappe.session.user:
            frappe.throw("Only the assigned Approver can submit this document.")

        if self.status not in ["Approved", "Rejected"]:
            frappe.throw("Status must be either Approved or Rejected before submitting.")

        created_checkins = []

        # Create checkins only if Approved
        if self.status == "Approved":

            # IN Checkin
            if self.change_in_time and self.applied_in_time:

                in_datetime = get_datetime(f"{self.date} {self.applied_in_time}")

                if not frappe.db.exists(
                    "Employee Checkin",
                    {"employee": self.employee, "time": in_datetime},
                ):
                    checkin_in = frappe.new_doc("Employee Checkin")
                    checkin_in.employee = self.employee
                    checkin_in.time = in_datetime
                    checkin_in.insert(ignore_permissions=True)

                    created_checkins.append(checkin_in.name)

            # OUT Checkin
            if self.change_out_time and self.applied_out_time:

                out_datetime = get_datetime(f"{self.date} {self.applied_out_time}")

                if not frappe.db.exists(
                    "Employee Checkin",
                    {"employee": self.employee, "time": out_datetime},
                ):
                    checkin_out = frappe.new_doc("Employee Checkin")
                    checkin_out.employee = self.employee
                    checkin_out.time = out_datetime
                    checkin_out.insert(ignore_permissions=True)

                    created_checkins.append(checkin_out.name)

        # Save created checkin IDs
        if created_checkins:
            self.db_set("employee_checkin_id", ", ".join(created_checkins))

    # ================= ON CANCEL =================

    def on_cancel(self):

        # 1Cancel Attendance if exists
        attendance = frappe.db.get_value(
            "Attendance",
            {
                "employee": self.employee,
                "attendance_date": self.date,
                "docstatus": 1,
            },
            "name",
        )

        if attendance:
            att_doc = frappe.get_doc("Attendance", attendance)
            att_doc.cancel()

        created_checkin_ids = []

        if self.employee_checkin_id:
            created_checkin_ids = [i.strip() for i in self.employee_checkin_id.split(",")]

        # Delete only created checkins
        for checkin_id in created_checkin_ids:

            if frappe.db.exists("Employee Checkin", checkin_id):

                checkin = frappe.get_doc("Employee Checkin", checkin_id)

                if checkin.docstatus == 1:
                    checkin.cancel()

                checkin.delete()

        # Modify other checkins of same employee & same date
        other_checkins = frappe.get_all(
            "Employee Checkin",
            filters={"employee": self.employee},
            fields=["name", "time"],
        )

        for chk in other_checkins:

            if (
                chk.time.date() == getdate(self.date)
                and chk.name not in created_checkin_ids
            ):
                frappe.db.set_value(
                    "Employee Checkin",
                    chk.name,
                    "modified",
                    now(),
                )

        # Clear field
        self.db_set("employee_checkin_id", None)

    # ================= DATE LOCK VALIDATION =================

    def validate_date_lock(self):

        settings = frappe.get_single("VCM HR Settings")

        if not settings.attendance_locked_upto:
            return

        locked_upto = getdate(settings.attendance_locked_upto)
        selected_date = getdate(self.date)
        today_date = getdate(today())

        allowed_from = add_days(locked_upto, 1)

        if selected_date <= locked_upto:
            frappe.throw(
                f"Attendance is locked upto {locked_upto}. "
                f"You can select date from {allowed_from} onwards."
            )

        if selected_date >= today_date:
            frappe.throw(
                "You cannot create Special Concession for today or future dates."
            )

    # ================= SHIFT TIME VALIDATION =================

    def validate_shift_time_range(self):

        if not self.employee_shift:
            return

        shift = frappe.get_doc("Shift Type", self.employee_shift)

        if not shift.start_time or not shift.end_time:
            return

        shift_start_dt = get_datetime(f"{self.date} {shift.start_time}")
        shift_end_dt = get_datetime(f"{self.date} {shift.end_time}")

        #  HANDLE NIGHT SHIFT (end < start means next day)
        is_night_shift = shift.end_time < shift.start_time

        if is_night_shift:
            shift_end_dt = add_days(shift_end_dt, 1)

        begin_before = shift.begin_check_in_before_shift_start_time or 0
        allow_after = shift.allow_check_out_after_shift_end_time or 0

        allowed_in_start = add_to_date(shift_start_dt, minutes=-begin_before)
        allowed_in_end = shift_end_dt

        allowed_out_start = shift_start_dt
        allowed_out_end = add_to_date(shift_end_dt, minutes=allow_after)

        # Validate IN time
        if self.change_in_time and self.applied_in_time:

            applied_in_dt = get_datetime(f"{self.date} {self.applied_in_time}")

            #  FIX: use get_time (no type error)
            if is_night_shift and get_time(self.applied_in_time) < get_time(shift.start_time):
                applied_in_dt = add_days(applied_in_dt, 1)

            if not (allowed_in_start <= applied_in_dt <= allowed_in_end):
                frappe.throw(
                    f"Applied In Time must be between "
                    f"{allowed_in_start.strftime('%d-%m %H:%M')} and {allowed_in_end.strftime('%d-%m %H:%M')}"
                )

        # Validate OUT time
        if self.change_out_time and self.applied_out_time:

            applied_out_dt = get_datetime(f"{self.date} {self.applied_out_time}")

            #  FIX: use get_time (no type error)
            if is_night_shift and get_time(self.applied_out_time) < get_time(shift.start_time):
                applied_out_dt = add_days(applied_out_dt, 1)

            if not (allowed_out_start <= applied_out_dt <= allowed_out_end):
                frappe.throw(
                    f"Applied Out Time must be between "
                    f"{allowed_out_start.strftime('%d-%m %H:%M')} and {allowed_out_end.strftime('%d-%m %H:%M')}"
                )