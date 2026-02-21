# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import getdate, nowdate, add_months, add_days, now_datetime, get_datetime
import logging


# Auto-attendance will only ever consider checkins from the current 
# payroll period (26th onward) (plus any HRMS “process_attendance_after” constraint).

# Late_checkin_lookback_days (narrow, fast)
# Handles:
# ✅ normal late punches
# ✅ out-of-order biometric sync
# ✅ routine processing
# 3–5 days
#  This runs hourly and must be fast.

# Repair_window_days (wider but controlled)
# Handles:
# ✅ Absent already marked
# ✅ edge corrections
# ✅ HR fixes
#  low frequency may be once a day but expensive

#  attendance_locked_upto  ← ERP System Manager controlled

# [Employee Checkin arrives]
#           ↓
# (lookback window applies here)
#           ↓
# [Auto Attendance creates Attendance]
#           ↓
# (Late punch discovered)
#           ↓
# (repair window applies here)
#           ↓
# [Attendance corrected]
#           ↓
# (payroll locked → freeze)

from hrms.hr.doctype.shift_type.shift_type import ShiftType as HRMSShiftType
logger = logging.getLogger(__name__)


def get_company_lock_and_windows(_company=None):
    """
    Source of truth: Single doctype 'VCM HR Settings'
    Fields:
      - attendance_locked_upto (Date)
      - late_checkin_lookback_days (Int)
      - repair_window_days (Int)
      - last_repair_run_at (Datetime)
    """
    settings = frappe.get_cached_doc("VCM HR Settings")  # cached, fast
    locked_upto = getdate(settings.attendance_locked_upto) if settings.attendance_locked_upto else None
    lookback_days = int(getattr(settings, "late_checkin_lookback_days", 4) or 4)
    repair_days = int(getattr(settings, "repair_window_days", 10) or 10)
    last_repair_run_at = get_datetime(settings.last_repair_run_at) if settings.last_repair_run_at else None     
    return locked_upto, lookback_days, repair_days, last_repair_run_at


class VCMShiftType(HRMSShiftType):
    # -----------------------------
    # Window 1: lookback for checkins
    # -----------------------------
    def get_effective_checkin_start_date(self):
        """
        This date limits routine hourly processing.
        - Small window for performance.
        - Still respects payroll lock (never touch locked dates).
        """
        logger.debug(f" in get_effective_checkin_start_date")
        # Shift Type itself doesn’t carry company; use default company for the "general" start date.
        # The stricter per-employee lock is enforced in reconcile step.
        default_company = (
            frappe.defaults.get_user_default("Company")
            or frappe.db.get_single_value("Global Defaults", "default_company")
        )
        locked_upto, lookback_days, _, _ = get_company_lock_and_windows()
        start = add_days(getdate(nowdate()), -lookback_days)
        if locked_upto:
            lock_start = add_days(locked_upto, 1)
            if lock_start > start:
                start = lock_start
        # Also respect HRMS process_attendance_after (if newer)
        if self.process_attendance_after:
            pa = getdate(self.process_attendance_after)
            if pa > start:
                start = pa
        return start

    def get_employee_checkins(self) -> list[dict]:
        """
        HRMS v15 signature-compatible override.
        We clamp the scan window by temporarily pushing process_attendance_after forward
        to our effective start date. HRMS base implementation already uses
        self.process_attendance_after internally.
        """
        effective_start = self.get_effective_checkin_start_date()        
        original = self.process_attendance_after
        logger.debug(f" effective start {effective_start}, origional {original}")
        try:
            if not original or getdate(original) < effective_start:
                self.process_attendance_after = effective_start
            return super().get_employee_checkins()
        finally:
            self.process_attendance_after = original

    # ---------------------------------------
    # Window 2: repair window for late arrivals
    # ---------------------------------------
    def process_auto_attendance(self, is_manually_triggered=False):
        logger.debug("in process_auto_attendance (forcing safe non-manual mode)")
        # Run standard HRMS auto attendance (creates/updates based on unlinked checkins)
        # Prevent HRMS from backfilling/marking mass absents during tests or routine ops
        super().process_auto_attendance(is_manually_triggered=False)
        # Then do targeted repair for late-arriving punches
        self.reconcile_late_checkins()

    def reconcile_late_checkins(self):
        """
        Repair strategy (delta-based):
        - Consider only checkins ingested/changed since last_repair_run_at
        - For those checkins, attempt to repair attendance for their attendance_date
        within repair_window_days
        - Never modify locked dates
        - Never modify Present attendance
        reconcile_late_checkins() does NOT create Attendance. It only:
            Cancels an existing non-Present Attendance (optional), and
            Sets Employee Checkin.attendance = NULL for that employee+date, so those checkins become “unlinked” again.
        HRMS ShiftType.process_auto_attendance() creates (or recreates) Attendance, because HRMS only 
            processes unlinked checkins (attendance is null) when it runs auto-attendance.
        DEBUG in console 
            shift = frappe.get_doc("Shift Type", "GENERAL9_30_to_6_00")
            shift.process_auto_attendance()
        """
        logger.debug(f" in reconcile_late_checkins ")
        locked_upto, _lookback_days, repair_days, last_run = get_company_lock_and_windows()
        logger.debug(f"locked_upto, _lookback_days, repair_days, last_run {locked_upto}, {_lookback_days}, {repair_days}, {last_run} ")
        today = getdate(nowdate())
        repair_floor = add_days(today, -repair_days)
        # First run safety: only look at last 1 day to to handle Null Last_run
        if last_run:
            from_dt = get_datetime(last_run)
        else:
            from_dt = add_days(now_datetime(), -1)
        # Fetch checkins changed since last run
        checkins = frappe.get_all(
            "Employee Checkin",
            filters={
                "modified": (">=", from_dt),
                "skip_auto_attendance": 0,
            },
            fields=["employee", "time", "attendance", "log_type", "shift"],
            order_by="time asc",
            limit_page_length=5000,  # safety cap; adjust if needed
        )
        if not checkins:
            # update last run timestamp anyway to avoid reprocessing same window
            logger.debug(" No last run found")
            frappe.db.set_value("VCM HR Settings", "VCM HR Settings", "last_repair_run_at", now_datetime())
            frappe.db.commit()
            return
        logger.debug(f"checkins_since_last_run count={len(checkins)} from_dt={from_dt} sample={checkins[:4]}")
        # bucket by (employee, attendance_date)
        buckets = set()
        for c in checkins:
            adate = getdate(c.time)
            # hard lock: never touch locked or earlier
            if locked_upto and adate <= locked_upto:
                logger.debug(f"adate <= locked_upto {adate}, {locked_upto} ")
                continue
            # repair window: don’t reopen very old dates
            if adate < repair_floor:
                logger.debug(f"adate < repair_floor {adate}, {repair_floor} ")
                continue
            buckets.add((c.employee, adate))
        if not buckets:
            logger.debug(f" no bucket")
            frappe.db.set_value("VCM HR Settings", "VCM HR Settings", "last_repair_run_at", now_datetime())
            frappe.db.commit()
            return
        updated_any = False
        for employee, adate in sorted(buckets):
            # If Attendance doesn't exist, skip (conservative).
            # If you want: create attendance from logs here later.
            attendance = frappe.get_value(
                "Attendance",
                {"employee": employee, "attendance_date": adate, "docstatus": ("<", 2)},
                ["name", "status", "docstatus"],
                as_dict=True,
            )
            if not attendance:
                logger.debug(f" no attendance")
                continue
            logger.debug(f" attendance  {attendance}, status {attendance.status}")
            # never touch Present
            if (attendance.status or "").strip().lower() == "present":
                continue
            logger.debug(f"repair_candidate employee={employee} date={adate} att={attendance.name} status={attendance.status} docstatus={attendance.docstatus}")

            # Cancel submitted attendance (Absent / Half Day etc.)
            if attendance.docstatus == 1:
                frappe.get_doc("Attendance", attendance.name).cancel()
                logger.debug(f"cancelling  {attendance.name}")
                updated_any = True
            # Unlink that day's checkins so HRMS can relink/recompute on next run
            frappe.db.sql(
                """
                UPDATE `tabEmployee Checkin`
                SET attendance = NULL
                WHERE employee = %s AND DATE(`time`) = %s
                """,
                (employee, adate),
            )
            updated_any = True
        # Update last run timestamp
        frappe.db.set_value("VCM HR Settings", "VCM HR Settings", "last_repair_run_at", now_datetime())
        if updated_any:
            frappe.db.commit()
        else:
            # still commit last_run update
            frappe.db.commit()


