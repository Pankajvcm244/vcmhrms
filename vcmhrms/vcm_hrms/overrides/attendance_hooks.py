# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import get_datetime, getdate, add_days, flt, cint, nowdate
from datetime import datetime, timedelta, time as pytime
import logging
logger = logging.getLogger(__name__)

def _to_time(t):
    """Shift start/end sometimes come as time / timedelta / string. Normalize to datetime.time."""
    if t is None:
        return None
    if isinstance(t, pytime):
        return t
    if isinstance(t, timedelta):
        total = int(t.total_seconds()) % 86400
        return pytime(hour=total // 3600, minute=(total % 3600) // 60, second=total % 60)
    parts = str(t).split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        return pytime(hour=h, minute=m, second=s)
    except Exception:
        return None


def _strict_shift_bounds_and_lunch(shift, attendance_date, in_dt=None, out_dt=None):
    """
    Returns:
      strict_start, strict_end, lunch_start, lunch_end
    All are datetimes; overnight-safe.
    lunch_* can be None if not configured.
    """
    st = _to_time(getattr(shift, "start_time", None))
    et = _to_time(getattr(shift, "end_time", None))
    if not st or not et:
        return None, None, None, None
    ls = _to_time(getattr(shift, "custom_lunch_start_time", None))
    le = _to_time(getattr(shift, "custom_lunch_end_time", None))
    def build(base_date):
        d = getdate(base_date)
        s = datetime.combine(d, st)
        e = datetime.combine(d, et)
        if e <= s:
            e = e + timedelta(days=1)
        lsd = led = None
        if ls and le:
            lsd = datetime.combine(d, ls)
            led = datetime.combine(d, le)
            # lunch might be after midnight in an overnight shift
            if lsd < s:
                lsd = lsd + timedelta(days=1)
            if led <= lsd:
                led = led + timedelta(days=1)
        return s, e, lsd, led
    d0 = getdate(attendance_date)
    s0, e0, l0s, l0e = build(d0)
    d1 = add_days(d0, -1)
    s1, e1, l1s, l1e = build(d1)
    # choose the best window that contains in/out
    if in_dt and (s0 <= in_dt <= e0 or (out_dt and s0 <= out_dt <= e0)):
        return s0, e0, l0s, l0e
    if in_dt and (s1 <= in_dt <= e1 or (out_dt and s1 <= out_dt <= e1)):
        return s1, e1, l1s, l1e
    # logger.debug(f"[_strict_shift_bounds_and_lunch 2] Could not determine strict bounds for attendance_date={attendance_date} in_dt={in_dt} out_dt={out_dt}. "
    #     f"Falling back to attendance_date window: s0={s0} e0={e0} lunch_start={l0s} lunch_end={l0e}")
    # fallback
    return s0, e0, l0s, l0e


def _shift_bounds_with_grace(shift, base_date):
    """
    Returns (win_start, win_end) for a shift on base_date, including grace minutes.

    - Uses Shift Type start_time / end_time (supports time, timedelta, string).
    - Handles overnight shifts (end <= start => end + 1 day).
    - Applies grace window:
        win_start = start_dt - begin_check_in_before... minutes
        win_end   = end_dt   + allow_check_out_after... minutes

    NOTE:
    For overnight shifts, the caller should try both base_date and base_date-1
    and pick the window that actually contains the punch datetime.
    """
    st = _to_time(getattr(shift, "start_time", None))
    et = _to_time(getattr(shift, "end_time", None))
    if not st or not et:
        return None, None
    d = getdate(base_date)
    start_dt = datetime.combine(d, st)
    end_dt = datetime.combine(d, et)
    if end_dt <= start_dt:
        end_dt = end_dt + timedelta(days=1)  # overnight shift
    pre = int(float(getattr(shift, "begin_check_in_before_shift_start_time", 0) or 0))
    post = int(float(getattr(shift, "allow_check_out_after_shift_end_time", 0) or 0))
    return (start_dt - timedelta(minutes=pre), end_dt + timedelta(minutes=post))

def apply_valid_window_in_out(doc, method=None):
    """
    Enforce "Valid IN/OUT" semantics inside the shift grace window.
    What this does:
    - Marks punches outside the allowed window as offshift=1 so HRMS ignores them.
    - We skip this logic for flexible shifts (custom_flexible_shift=1), where you want pure duration-based hours.
    - For punches inside the window, deterministically sets:
        For non-flexible shifts:
            - Only FIRST punch in window is IN, skip_auto_attendance=0, 
            - Only LAST  punch in window is OUT, skip_auto_attendance=0, 
            - All middle punches are ignored log_type="", skip_auto_attendance=1, 
            This prevents HRMS “IN/OUT pairing” from producing short working_hours
    when users add an extra punch in between.
      (HRMS then calculates working hours based on valid IN/OUT)
    Critical fixes included:
    - Overnight shifts: window might belong to previous attendance date.
    - Manual/test inserts: include the current doc in candidate set even if it's not in DB yet.
    - Deterministic: every validate recomputes first/last across the full candidate list.
    """
    # Prevent recursion if any downstream code triggers validate again
    # logger.debug(f"[apply_valid_window_in_out 1] Validating {doc.name} time={getattr(doc,'time',None)} shift={getattr(doc,'shift',None)} employee={getattr(doc,'employee',None)}")
    if getattr(frappe.flags, "vcm_inout_guard", False):
        # logger.debug(f"[apply_valid_window_in_out 2] Guard active, skipping validation for {doc.name}")   
        return
    # Must have employee + time + shift
    if not doc.employee or not doc.time or not doc.shift:
        # logger.debug(f"[apply_valid_window_in_out 3] Missing employee/time/shift, skipping validation for {doc.name}")    
        return
    try:
        frappe.flags.vcm_inout_guard = True
        shift = frappe.get_cached_doc("Shift Type", doc.shift)
        # Optional: if you really want ONE flexible shift where pure duration counts,
        # We need to force IN/OUT otheriwse IN OUT will be missing.
        #  we need to force IN Out so that hrms takes it better
        # if int(getattr(shift, "custom_flexible_shift", 0) or 0):
        #     logger.debug(f"[apply_valid_window_in_out 4] Shift {shift.name} is flexible; skipping valid window logic")
        #     return
        
        # ----------------------------
        # 1)  Resolve correct grace window for this punch  (overnight-safe)
        #    Try both punch_date and punch_date-1; pick the one containing punch_dt.
        # ----------------------------
        punch_dt = get_datetime(doc.time)
        if not punch_dt:
            # logger.debug(f"[apply_valid_window_in_out 5] Invalid punch datetime for {doc.name}: {doc.time}")    
            return
        # ------------------------------------------------------------
        # 1) Resolve correct grace window for this punch (overnight-safe)
        # ------------------------------------------------------------
        d0 = getdate(punch_dt)
        w0_start, w0_end = _shift_bounds_with_grace(shift, d0)
        d1 = add_days(d0, -1)
        w1_start, w1_end = _shift_bounds_with_grace(shift, d1)
        w_start, w_end = w0_start, w0_end
        if w1_start and w1_end and w1_start <= punch_dt <= w1_end:
            w_start, w_end = w1_start, w1_end
        if not w_start or not w_end:
            # logger.debug(f"[apply_valid_window_in_out 6] Could not determine grace window for {doc.name} on date {d0} or {d1}. Skipping validation.")
            return
        # logger.debug(f"[apply_valid_window_in_out] Validating checkin {doc.name} time={punch_dt} ")
        # Outside grace window => ignored by HRMS
        if punch_dt < w_start or punch_dt > w_end:
            doc.offshift = 1
            # logger.debug(f"[apply_valid_window_in_out 7] Punch {doc.name} at {punch_dt} is outside grace window ({w_start} to {w_end}). Marking offshift=1.")   
            return
        # Inside window => always on-shift, but log_type (IN/OUT) to be determined by position in window
        doc.offshift = 0 
        # If this is an unsaved doc without a name yet, we cannot deterministically
        # reclassify the entire window (because we cannot include it safely).
        # Next validate after insert will reclassify correctly.
        if not doc.name:
            # logger.debug(f"[apply_valid_window_in_out 8] Doc {doc} has no name yet. Skipping deterministic IN/OUT classification for now; will apply on next validate after insert.")
            return
        
        # ------------------------------------------------------------
        # 2) Fetch other in-window punches (DB) and include current doc
        # ------------------------------------------------------------
        # IMPORTANT: do not rely on DB alone because current doc may not be committed yet.
        filters = {
            "employee": doc.employee,
            "shift": doc.shift,
            # "offshift": 0,                 # only in-window ones
            # don't filter skip_auto_attendance here; we want to reclassify everything deterministically
            "time": ("between", [w_start, w_end]),
        }
        # Exclude current name if it exists (avoid duplicates)
        if doc.name:
            filters["name"] = ("!=", doc.name)
        db_logs = frappe.get_all(
            "Employee Checkin",
            filters=filters,
            fields=["name", "time", "skip_auto_attendance", "log_type"],
            order_by="time asc",
            limit_page_length=5000,
        )
        # If nothing in DB yet, let the next validate classify.
        if not db_logs:
            # logger.debug(f"[apply_valid_window_in_out 9] No existing punches in DB for employee {doc.employee} shift {doc.shift} between {w_start} and {w_end}. Skipping deterministic IN/OUT classification for now; will apply on next validate after insert.")   
            return
        
        # Build candidate list INCLUDING current doc even if not in DB yet
        candidates = list(db_logs)
        # If current doc isn't present in db_logs (typical during insert validate), add it
        if doc.name and all(r["name"] != doc.name for r in candidates):
            candidates.append({"name": doc.name, "time": punch_dt})
        # Sort by time to find first/last deterministically
        candidates.sort(key=lambda r: get_datetime(r["time"]))
        if not candidates:
            # logger.debug(f"[apply_valid_window_in_out 10] No candidates found after including current doc {doc.name}. This should not happen as we checked db_logs already. Skipping classification.")  
            return
        first_name = candidates[0]["name"]
        last_name = candidates[-1]["name"]
        # logger.debug(
        #     f"[apply_valid_window_in_out] Candidates count={len(candidates)} first={first_name}({candidates[0]['time']}) "
        #     f"last={last_name}({candidates[-1]['time']})"
        # )
        # Helper: update DB rows only (avoid db.set_value on doc that isn't inserted yet)
        db_names = {r["name"] for r in db_logs}
        def _set_db(name: str, values: dict):
            if name in db_names:
                frappe.db.set_value("Employee Checkin", name, values, update_modified=False)
        # Re-classify all candidates every time (prevents "two OUTs" leftovers)
        for r in candidates:
            name = r["name"]
            if name == first_name and name == last_name:
                # Only one punch in window (single-entry day): mark as IN, keep it usable
                if name == doc.name:
                    doc.log_type = "IN"
                    doc.skip_auto_attendance = 0
                    doc.offshift = 0
                _set_db(name, {"log_type": "IN", "skip_auto_attendance": 0, "offshift": 0})
                continue
            if name == first_name:
                if name == doc.name:
                    doc.log_type = "IN"
                    doc.skip_auto_attendance = 0
                    doc.offshift = 0
                _set_db(name, {"log_type": "IN", "skip_auto_attendance": 0, "offshift": 0})
            elif name == last_name:
                if name == doc.name:
                    doc.log_type = "OUT"
                    doc.skip_auto_attendance = 0
                    doc.offshift = 0
                _set_db(name, {"log_type": "OUT", "skip_auto_attendance": 0, "offshift": 0})
            else:
                # Middle punch: ignore for auto-attendance but keep offshift clean
                if name == doc.name:
                    doc.log_type = ""
                    doc.skip_auto_attendance = 1
                    doc.offshift = 0
                _set_db(name, {"log_type": "", "skip_auto_attendance": 1, "offshift": 0})
        # logger.debug(
        #     f"[apply_valid_window_in_out] Final for {doc.name}: log_type={getattr(doc,'log_type',None)} "
        #     f"skip={getattr(doc,'skip_auto_attendance',None)} offshift={getattr(doc,'offshift',None)}"
        # )
    finally:
        frappe.flags.vcm_inout_guard = False


def apply_nonflex_strict_hours(doc, method=None):
    """
    NON-flex shift enforcement (post-HRMS, but before submit):
    Goals:
    - Clamp WORKING HOURS to strict shift bounds (NO grace).
    - Compute late flags:
        * custom_late_entry_within_grace = 1 only when (strict_start < in_time <= strict_start + late_grace)
          (this is what your quota counts)
        * late_entry = 1 for any late (in_time > strict_start)  [optional but useful]
    - Compute early_exit using strict_end - early_exit_grace.
    - Recompute status deterministically:
        * Never override leave-driven / holiday / cancelled / flexible
        * Never mark Present if early_exit=1
        * Use BOTH:
            (A) strict-hours thresholds (absent_thr / half_day_thr)
            (B) half-day structure using lunch window (first half / second half completion)

    Notes:
    - Requires helper: _strict_shift_bounds_and_lunch(shift, attendance_date, in_dt, out_dt)
      returning (strict_start, strict_end, lunch_start, lunch_end) with lunch_* possibly None.
    - If lunch_* missing, we fallback to 4h + 30m lunch.
    """
    # logger.debug(f"[apply_nonflex_strict_hours 1] Evaluating {doc.name} for non-flex strict hours enforcement")     
    # Ignore attendance created by sandwich logic to prevent conflicts.
     # 🚨 ONLY run during auto attendance rebuild, this flag is set in reconcile_late_checkin
    # if not getattr(frappe.flags, "in_auto_attendance_run", False):
    #     logger.debug(f"[apply_nonflex_strict_hours 1.1.0] Flag not set") 
    #     return
    if getattr(doc, "docstatus", 0) == 2:
        # logger.debug(f"[apply_nonflex_strict_hours 1.0.0.] docstatus 2")
        return
    # Must have context + in/out
    if not getattr(doc, "shift", None) or not getattr(doc, "attendance_date", None):
        # var1 = getattr(doc, "shift", None)
        # var2 = getattr(doc, "attendance_date", None)
        # logger.debug(f"[apply_nonflex_strict_hours 1.1.1.1] {var1} , {var2} return 1")
        return
    if not getattr(doc, "in_time", None) or not getattr(doc, "out_time", None):
        # logger.debug(f"[apply_nonflex_strict_hours 1.1.1.2]  , return 1")
        return
    shift = frappe.get_cached_doc("Shift Type", doc.shift)
    
    if cint(getattr(shift, "custom_flexible_shift", 0) or 0):
        # logger.debug(f"[apply_nonflex_strict_hours 2] Shift {shift.name} is flexible; skipping strict hours enforcement for {doc.name}")
        return
    # logger.debug(f"[apply_nonflex_strict_hours 3] Shift {shift.name} is non-flex; applying strict hours enforcement for {doc.name}")
    # Skip leave-driven attendance
    status_lc = (getattr(doc, "status", "") or "").strip().lower()
    leave_type = (getattr(doc, "leave_type", "") or "").strip()
    #  On leave status if for full day leave, for half day it is "Half Day"
    #  values for status are Present, Absent, On Leave, Half Day
    # values for other half is Present, Absent
    if status_lc in ("on leave", "holiday"):
        # Skip as this is full day leave
        # 👇 Check if it is Sandwich Absent
        if leave_type != "Sandwich Absent":
            # handle non sandwich leave case return else continue
            return  # normal leave → skip
        
    #  on leave day we may have s=checkin logs, so still process and mark flags
    if getattr(doc, "leave_application", None) or getattr(doc, "leave_type", None):
        # allow processing if actual punches exist
        if not (getattr(doc, "in_time", None) and getattr(doc, "out_time", None)):
            # logger.debug(f"[apply_nonflex_strict_hours 1.1.1.3] return ")
            return
    # logger.debug(f"[apply_nonflex_strict_hours 1.1] Evaluating {doc.name} for non-flex strict hours enforcement") 
    in_dt = get_datetime(doc.in_time)
    out_dt = get_datetime(doc.out_time)
    if not in_dt or not out_dt or out_dt <= in_dt:
        # logger.debug(f"[apply_nonflex_strict_hours 1.2] in and out date/time missing") 
        return
    strict_start, strict_end, lunch_start, lunch_end = _strict_shift_bounds_and_lunch(
        shift=shift,
        attendance_date=doc.attendance_date,
        in_dt=in_dt,
        out_dt=out_dt,
    )
    # logger.debug(
    #     f"[apply_nonflex_strict_hours 2] For in_dt={in_dt} out_dt={out_dt} strict_start={strict_start} strict_end={strict_end} lunch_start={lunch_start} lunch_end={lunch_end}"
    # )
    if not strict_start or not strict_end:
        # logger.debug(f"[apply_nonflex_strict_hours 1.1.1.4] return {strict_start}, {strict_end}")
        return

    # Fallback lunch window if not configured on shift
    if not lunch_start or not lunch_end:
        lunch_start = strict_start + timedelta(hours=4)
        lunch_end = lunch_start + timedelta(minutes=30)

    # ----------------------------
    # Clamp working hours (strict)
    # ----------------------------
    eff_in = max(in_dt, strict_start)
    eff_out = min(out_dt, strict_end)
    seconds = max(0, (eff_out - eff_in).total_seconds())
    wh = flt(seconds / 3600.0, 2)

    if flt(doc.working_hours or 0, 2) != wh:
        # logger.debug(
        #     f"[apply_nonflex_strict_hours 3 Updating WH] {doc.name} "
        #     f"actual_in={in_dt} actual_out={out_dt} "
        #     f"eff_in={eff_in} eff_out={eff_out} wh={wh}"
        # )
        doc.working_hours = wh
    # ----------------------------
    # Late entry flags
    # ----------------------------
    # Your quota is ONLY for "late but within grace"
    if cint(getattr(shift, "enable_late_entry_marking", 0) or 0):
        late_grace = cint(getattr(shift, "late_entry_grace_period", 0) or 0)
        grace_cutoff = strict_start + timedelta(minutes=late_grace)       
        # Only compute within_grace, DO NOT overwrite if already set
        computed_within_grace = 1 if (in_dt > strict_start and in_dt <= grace_cutoff) else 0
        existing_flag = cint(getattr(doc, "custom_late_entry_within_grace", 0) or 0)
        # quota is source of truth
        final_flag =existing_flag
        doc.custom_late_entry_within_grace = final_flag
        # logger.debug(
        #     f"[apply_nonflex_strict_hours 4] within_grace={computed_within_grace} for {doc.name}: "
        #     f"in_dt={in_dt} strict_start={strict_start} grace_cutoff={grace_cutoff}"
        # )
        # keep a general late flag (useful for reporting; policy should use within_grace)
        doc.late_entry = 1 if (in_dt > strict_start) else 0
        # logger.debug(
        #     f"[ATT-CLAMP 4] late_entry={doc.late_entry} for {doc.name}: "
        #     f"in_dt={in_dt} strict_start={strict_start}"
        # )
    else:
        doc.late_entry = 0
        if hasattr(doc, "custom_late_entry_within_grace"):
            doc.custom_late_entry_within_grace = 0
    # ----------------------------
    # Early exit flag (strict)
    # ----------------------------
    if cint(getattr(shift, "enable_early_exit_marking", 0) or 0):
        early_grace = cint(getattr(shift, "early_exit_grace_period", 0) or 0)
        doc.early_exit = 1 if (out_dt < strict_end - timedelta(minutes=early_grace)) else 0
    else:
        doc.early_exit = 0
    # ----------------------------
    # Half completion structure using lunch window
    # ----------------------------
    # First half considered "completed" only if employee worked for 4 hours or 3.5 hour if 3 days delay adjustment.
    first_half_start = strict_start
    first_half_end = lunch_start
    # Calculate overlap between [eff_in, eff_out] and [first_half_start, first_half_end]
    overlap_start = max(eff_in, first_half_start)
    overlap_end = min(eff_out, first_half_end)
    first_half_work_seconds = max(0,(overlap_end - overlap_start).total_seconds() )
    first_half_hours = first_half_work_seconds / 3600.0
    # Default strict threshold
    required_first_half_hours = (lunch_start - strict_start).total_seconds() / 3600
    # If late but within grace → allow 30 min relaxation
    # Only allow relaxation if quota still available
    allow_relaxation = 0
    if cint(getattr(doc, "custom_late_entry_within_grace", 0)):
        #  Pankaj changed it to fix late entry free quota
        # 1. Auto attendance runs
        # → many days may temporarily get within_grace = 1
        # 2. reconcile_late_grace_quota()
        # → trims to allowed quota
        # → resets extra days to 0
        # 3. apply_nonflex_strict_hours re-runs
        # → statuses corrected
        # logger.debug(f"[apply_nonflex_strict_hours 1-1-1]  allow_relaxation =1 ")
        allow_relaxation = 1
        # settings = frappe.get_cached_doc("VCM HR Settings")
        # allowed = cint(getattr(settings, "allowed_late_entry_count", 0) or 0)
        # period_start, period_end = _payroll_period_for_date(doc.attendance_date)
        # used_other = frappe.db.count(
        #     "Attendance",
        #     {
        #         "employee": doc.employee,
        #         "docstatus": ["!=", 2],
        #         "attendance_date": ["between", [period_start, period_end]],
        #         "custom_late_entry_within_grace": 1,
        #         "status": ["in", ["Present", "Half Day"]],
        #         "name": ["!=", doc.name],
        #     },
        # )
        # if used_other < allowed:
        #     allow_relaxation = 1
        # else:
        #     #  Quota exhausted → reset flag immediately
        #     # logger.debug(
        #     #     f"[apply_nonflex_strict_hours 4] Quota exhausted. Resetting custom_late_entry_within_grace for {doc.name}"
        #     # )
        #     doc.custom_late_entry_within_grace = 0
    if allow_relaxation:
        required_first_half_hours = max(0, required_first_half_hours - late_grace / 60)
    first_half_done = 1 if first_half_hours >= required_first_half_hours else 0
    # logger.debug(f"[apply_nonflex_strict_hours 5]  wh={wh} first_half_hours={first_half_hours} required_first_half_hours={required_first_half_hours} first_half_done={first_half_done}")

    # Second half considered "completed" only if employee was present at/after lunch_end AND stayed till strict_end.
    # This enforces your "second half has no quota and must complete full 4 hours".
    # SECOND HALF
    second_half_hours = 0
    if eff_out > lunch_end:
        sh_start = max(eff_in, lunch_end)
        second_half_hours = max(0, (eff_out - sh_start).total_seconds()) / 3600
    required_second_half_hours = (strict_end - lunch_end).total_seconds() / 3600
    second_half_done = 1 if second_half_hours >= required_second_half_hours else 0
    # logger.debug(f"[apply_nonflex_strict_hours 6]  wh={wh} second_half_hours={second_half_hours} required_second_half_hours={required_second_half_hours} second_half_done={second_half_done}")
    # ----------------------------
    # Persist Half Completion Flags
    # ----------------------------
    if hasattr(doc, "custom_first_half_done"):
        doc.custom_first_half_done = first_half_done
    if hasattr(doc, "custom_second_half_done"):
        doc.custom_second_half_done = second_half_done
    # logger.debug(
    #     f"[apply_nonflex_strict_hours 7] first_half_done={doc.custom_first_half_done} "
    #     f"second_half_done={doc.custom_second_half_done}"
    # )
    # ----------------------------
    # Recompute STATUS
    # Priority:
    #   1) strict-hour thresholds (Absent/Half Day)
    #   2) structure (Present requires BOTH halves)
    #   3) early_exit blocks Present (max Half Day)
    # Never override existing Absent -> Present later here.
    # ----------------------------
    # NEVER override leave-driven attendance
    is_leave_attendance = (
        getattr(doc, "leave_application", None)
        or getattr(doc, "leave_type", None)
        or (doc.status or "").strip().lower() == "on leave"
    )
   
    if (doc.status or "").strip().lower() == "on leave":
        # Skip only full leave, half day leave may have punch also and we
        #  need to process that attendance and mark half day status as present in that case
        # logger.debug(f"[apply_nonflex_strict_hours 7.1] Attendance {doc.name} is marked as 'On Leave'. Skipping status override but will evaluate half-day status if applicable.")
        return
    absent_thr = getattr(shift, "working_hours_threshold_for_absent", None)
    half_day_thr = getattr(shift, "working_hours_threshold_for_half_day", None)
    if absent_thr is None or half_day_thr is None:
        return
    absent_thr = float(absent_thr)
    half_day_thr = float(half_day_thr)
    # Start from strict-hour decision
    if not first_half_done and not second_half_done:
        if doc.leave_application:
            # do nothing, this case is when half day leave is coming with 1-2 hour punch
            # leave already made it half or full day, if attendence comes don't change Status
            # logger.debug(f"[apply_nonflex_strict_hours 7.2] {doc.name} existing status '{doc.status}' without overriding  Absent.")
            new_status = doc.status
        else:
            new_status = "Absent"
    elif first_half_done and second_half_done:
        new_status = "Present"
    else:
        new_status = "Half Day"
    # Early exit can never be Present (best case Half Day)
    if cint(getattr(doc, "early_exit", 0) or 0) and new_status == "Present":
        # logger.debug(f"[apply_nonflex_strict_hours 9] {doc.name} has early exit. Downgrading from Present to Half Day.")    
        new_status = "Half Day"
    # Apply if changed
    if (doc.status or "").strip() != new_status:
        # logger.debug(
        #     f"[apply_nonflex_strict_hours 11] Status override {doc.name}: old={doc.status} new={new_status} "
        #     f"wh={wh} first_half_done={first_half_done} second_half_done={second_half_done} early_exit={getattr(doc,'early_exit',None)}"
        # )
        doc.status = new_status
    # logger.debug(f"[apply_nonflex_strict_hours 11-1] Final status for {doc.name}: {doc.status} with wh={wh} first_half_done={first_half_done} second_half_done={second_half_done} early_exit={getattr(doc,'early_exit',None)}")
    # Always evaluate half_day_status when status is Half Day
    if hasattr(doc, "half_day_status"):
        # logger.debug(f"[apply_nonflex_strict_hours 11.1] Evaluating half_day_status for {doc.name} with new_status={new_status}")
        if new_status != "Half Day":
            #  if full present or full day leave we don't need half day status, so clearing that field to avoid confusion
            # logger.debug(f"[apply_nonflex_strict_hours 11.1.1] {doc.name} is not Half Day. Clearing half_day_status.") 
            doc.half_day_status = None
        else:
            # half day leave + punch case
            # if employee applied hald day leave and then hald day punch comes
            #  we need to mark half day status as present because leave application
            #  will create attendance with half day status as present and if punch comes 
            #  after that we should not mark it as absent
            # logger.debug(f"[apply_nonflex_strict_hours 11.2] Marking half_day_status for {doc.name} based on leave application and punch data: leave_application={doc.leave_application} leave_type={doc.leave_type} first_half_done={first_half_done} second_half_done={second_half_done}")
            if doc.leave_application or doc.leave_type:
                # sometime even without punch half day leave was makign other half present, so now w eare checking punch also.
                has_punch = bool(doc.in_time and doc.out_time)
                if has_punch and (first_half_done or second_half_done):
                    # logger.debug(f"[apply_nonflex_strict_hours 12] punch {has_punch} (doc.in_time={doc.in_time}, doc.out_time={doc.out_time}) Marking half_day_status as Present for {doc.name} due to half-day leave application")
                    doc.half_day_status = "Present"
                    doc.db_set("half_day_status", "Present", update_modified=False)
                    # logger.debug(
                    #     f"[HALF DAY FINAL] {doc.name} status={doc.status} half_day_status={doc.half_day_status}"
                    # )
                else:
                    # logger.debug(f"[apply_nonflex_strict_hours 12-1] No valid punch (doc.in_time={doc.in_time}, doc.out_time={doc.out_time}) Marking half_day_status as Absent for {doc.name} despite half-day leave application")
                    # 🚨 DO NOT downgrade if already Present (multi-run safe)
                    # if doc.half_day_status != "Present":
                    doc.half_day_status = "Absent"
            else:
                # logger.debug(f"[apply_nonflex_strict_hours 12-1-1] Marking half_day_status as Absent for {doc.name} due to no half-day leave application")
                doc.half_day_status = "Absent"  
    # else:
    #     logger.debug(f"[apply_nonflex_strict_hours 11] No status change for {doc.name}. Status remains {doc.status} with wh={wh} first_half_done={first_half_done} second_half_done={second_half_done} early_exit={getattr(doc,'early_exit',None)}")




def apply_flex_min_hours(doc, method=None):
    # Correct Decision Matrix
    # Scenario	Expected Result
    # Full day leave + no punch	On Leave
    # Full day leave + punch	On Leave (OT allowed but not changing status)
    # Half day leave + no punch	Half Day + Absent
    # Half day leave + punch	Half Day (worked half)
    # No leave	Normal flex logic
    logging.debug(f"[apply_flex_min_hours] Evaluating {doc.name}")
    # if not getattr(frappe.flags, "in_auto_attendance_run", False):
    #     logger.debug(f"[apply_flex_min_hours] Not in auto attendance run; skipping flexible hours enforcement for {doc.name}")
    #     return
    if not doc.shift:
        return
    if getattr(doc, "docstatus", 0) == 2:
        return
    if not doc.shift or not doc.in_time or not doc.out_time:
        return
    shift = frappe.get_cached_doc("Shift Type", doc.shift)
    # ✅ ONLY FLEX HERE
    if not cint(getattr(shift, "custom_flexible_shift", 0)):
        # logger.debug(f"[apply_flex_min_hours] Shift {shift.name} is not flexible; skipping flex hours enforcement for {doc.name}")
        return
    # =====================================================
    # 🚨 CRITICAL: HANDLE LEAVE FIRST
    # =====================================================
    is_leave = bool(doc.leave_application or doc.leave_type)
    # -------------------------
    # FULL DAY LEAVE
    # -------------------------
    if is_leave and (doc.status or "").strip().lower() == "on leave":
        # logging.debug(f"[FLEX SKIP] Full day leave → {doc.name}")
        # 👉 Optional: compute OT but DO NOT change status
        if doc.in_time and doc.out_time:
            in_dt = get_datetime(doc.in_time)
            out_dt = get_datetime(doc.out_time)
            if in_dt and out_dt and out_dt > in_dt:
                wh = flt((out_dt - in_dt).total_seconds() / 3600.0, 2)
                doc.working_hours = wh  # for reporting only
        return  # 🔥 DO NOT TOUCH STATUS
    # -------------------------
    # HALF DAY LEAVE
    # -------------------------
    absent_thr = float(getattr(shift, "working_hours_threshold_for_absent", 0) or 0)
    half_day_thr = float(getattr(shift, "working_hours_threshold_for_half_day", 0) or 0)
    if is_leave and (doc.status or "").strip().lower() == "half day":
        # logging.debug(f"[FLEX HALF LEAVE] {doc.name}")
        # no punch → absent half
        if not doc.in_time or not doc.out_time:
            doc.working_hours = 0
            doc.half_day_status = "Absent"
            return
        # punch exists → worked half
        in_dt = get_datetime(doc.in_time)
        out_dt = get_datetime(doc.out_time)
        if not in_dt or not out_dt or out_dt <= in_dt:
            return
        wh = flt((out_dt - in_dt).total_seconds() / 3600.0, 2)
        doc.working_hours = wh
        # first check working hour satisfies for half or full day
        #  if min half day hour are satisfied then only other half_day_status is present
        if wh > absent_thr:
            # logging.debug(f"[FLEX HALF LEAVE 1] wh {wh} other half Present")
            doc.half_day_status = "Present"
        else:
            # logging.debug(f"[FLEX HALF LEAVE 2] wh {wh} other half Absent")
            doc.half_day_status = "Absent"
        return
    # =====================================================
    # NORMAL FLEX LOGIC (NO LEAVE)
    # =====================================================
    if not doc.in_time or not doc.out_time:
        return
    in_dt = get_datetime(doc.in_time)
    out_dt = get_datetime(doc.out_time)
    if not in_dt or not out_dt or out_dt <= in_dt:
        return
    wh = flt((out_dt - in_dt).total_seconds() / 3600.0, 2)
    doc.working_hours = wh   
    # logging.debug(
    #     f"[FLEX] {doc.name} wh={wh} absent_thr={absent_thr} half_day_thr={half_day_thr}"
    # )
    if wh < absent_thr:
        new_status = "Absent"
    elif wh < half_day_thr:
        new_status = "Half Day"
        # first check working hour satisfies for half or full day
        #  if min half day hour are satisfied then only other half_day_status is present        
    else:
        new_status = "Present"
    if doc.status != new_status:
        doc.status = new_status
    if hasattr(doc, "half_day_status"):
        if new_status == "Half Day": 
            # In this case if only punch is making it half day then other half will be Absent
            #  only leave + punch can make other half as present   
            # logging.debug(f"[FLEX NORMAL 4] wh {wh} other half Absent")
            doc.half_day_status = "Absent"
        else:
            doc.half_day_status = None
    # logger.debug(
    #     f"[FLEX] {doc.name} status={doc.status} wh={wh}"
    # )


def _strict_shift_bounds_for_attendance(shift, attendance_date, in_dt, out_dt):
    """
    Compute strict shift bounds (NO grace) for this Attendance.
    Overnight-safe:
    - We try base_date = attendance_date and attendance_date - 1,
      and pick the strict window that best fits the in/out times.
    """
    st = _to_time(getattr(shift, "start_time", None))
    et = _to_time(getattr(shift, "end_time", None))
    if not st or not et:
        return None, None
    def strict_window(base_date):
        d = getdate(base_date)
        s = datetime.combine(d, st)
        e = datetime.combine(d, et)
        if e <= s:
            e = e + timedelta(days=1)
        return s, e
    d0 = getdate(attendance_date)
    s0, e0 = strict_window(d0)
    d1 = add_days(d0, -1)
    s1, e1 = strict_window(d1)
    # Choose the window that contains either in_dt or out_dt (prefer containment)
    if s0 <= in_dt <= e0 or s0 <= out_dt <= e0:
        return s0, e0
    if s1 <= in_dt <= e1 or s1 <= out_dt <= e1:
        return s1, e1
    # Fallback: choose the closer one by start distance
    if abs((in_dt - s1).total_seconds()) < abs((in_dt - s0).total_seconds()):
        return s1, e1
    return s0, e0

def _payroll_period_for_date(attendance_date):
    """
    Payroll/late-quota window derived from VCM HR Settings -> Attendance Locked Upto.
    Rule:
      - If locked_upto exists: current open period starts from locked_upto + 1 day
      - Period end is the given attendance_date (running quota up to that day)
      - If locked_upto is empty: fallback to last 31 days (first-time safety)
    """
    settings = frappe.get_cached_doc("VCM HR Settings")
    d = getdate(attendance_date)
    locked_upto = getdate(settings.attendance_locked_upto) if settings.attendance_locked_upto else None
    if locked_upto:
        return add_days(locked_upto, 1), d
    # First-time setup fallback
    return add_days(d, -31), d

def enforce_late_entry_policy(doc, method=None):
    """
    Policy (NON-flexible shifts only):

    Rules:
      1) Never override Absent
      2) Only applies when custom_late_entry_within_grace = 1
      3) Quota applies ONLY to first-half lateness
      4) When quota exhausted:
            - Reset grace flag
            - Remove first-half relaxation effect
            - Recompute status strictly
      5) Early exit applied LAST:
            - if first_half_done → Half Day
            - else → Absent
    """
    # ---------------------------------------------------------
    # Basic Guards
    # ---------------------------------------------------------
    if cint(getattr(doc, "custom_created_by_sandwich", 0)):
        return
    if getattr(doc, "docstatus", 0) == 2:
        return
    if not (doc.employee and doc.attendance_date and doc.shift):
        return
    status_lc = (doc.status or "").strip().lower()
    # Never override Absent
    if status_lc == "absent":
        # logger.debug(f"[enforce_late_entry_policy] Skipping {doc.name} because status is Absent")
        return
    if status_lc == "on leave" or doc.leave_application or doc.leave_type:
        return
    shift = frappe.get_cached_doc("Shift Type", doc.shift)
    if cint(getattr(shift, "custom_flexible_shift", 0)):
        return
    # ---------------------------------------------------------
    # Must be late within grace to apply quota logic
    # ---------------------------------------------------------
    within_grace = cint(getattr(doc, "custom_late_entry_within_grace", 0) or 0)
    if not within_grace:
        return  # Because the policy only applies to grace lateness
    else:
        # ---------------------------------------------------------
        # Validate true grace window
        # ---------------------------------------------------------
        in_dt = get_datetime(doc.in_time)
        if not in_dt:
            return
        strict_start, strict_end = _strict_shift_bounds_for_attendance(
            shift=shift,
            attendance_date=doc.attendance_date,
            in_dt=in_dt,
            out_dt=get_datetime(doc.out_time) or in_dt,
        )
        if not strict_start:
            return
        late_grace = cint(getattr(shift, "late_entry_grace_period", 0) or 0)
        grace_cutoff = strict_start + timedelta(minutes=late_grace)
        if not (in_dt > strict_start and in_dt <= grace_cutoff):
            return
        # ---------------------------------------------------------
        # Must complete second half strictly for any Present upgrade
        # ---------------------------------------------------------
        second_half_done = cint(getattr(doc, "custom_second_half_done", 0) or 0)
        if second_half_done == 0:
            return
        # ---------------------------------------------------------
        # Must meet absent threshold
        # ---------------------------------------------------------
        absent_thr = flt(getattr(shift, "working_hours_threshold_for_absent", 0) or 0, 2)
        wh = flt(doc.working_hours or 0, 2)
        if wh < absent_thr:
            return
        # ---------------------------------------------------------
        # Quota Counting
        # ---------------------------------------------------------
        settings = frappe.get_cached_doc("VCM HR Settings")
        allowed = cint(getattr(settings, "allowed_late_entry_count", 0) or 0)
        period_start, period_end = _payroll_period_for_date(doc.attendance_date)
        used_other = frappe.db.count(
            "Attendance",
            {
                "employee": doc.employee,
                "docstatus": 1,
                "attendance_date": ["between", [period_start, period_end]],
                "custom_late_entry_within_grace": 1,
                "status": ["in", ["Present", "Half Day"]],
                "name": ["!=", doc.name],
            },
        )
        effective_used = used_other + 1
        # ---------------------------------------------------------
        # OVER QUOTA → RESET FLAGS + STRICT RECOMPUTE
        # ---------------------------------------------------------
        if effective_used > allowed:
            # logger.debug(
            #     f"[enforce_late_entry_policy 1] Quota exhausted for {doc.name}. Resetting grace flags."
            # )
            # Reset grace effect
            doc.custom_late_entry_within_grace = 0
            # Remove relaxed first-half completion
            if hasattr(doc, "custom_first_half_done"):
                doc.custom_first_half_done = 0
            first_half_done = 0
            second_half_done = cint(getattr(doc, "custom_second_half_done", 0) or 0)
            # Strict recompute
            if not first_half_done and not second_half_done:
                doc.status = "Absent"
            elif first_half_done and second_half_done:
                doc.status = "Present"
            else:
                doc.status = "Half Day"
             # ---------------------------------------------------------
            # Fix half-day status logic
            # ---------------------------------------------------------
            if hasattr(doc, "half_day_status"):
                # logger.debug(
                #     f"[enforce_late_entry_policy 2] Recomputing half_day_status for {doc.name} after quota exhaustion. new_status={doc.status}" )
                if doc.status == "Half Day":
                    # If half-day leave + employee worked → Present
                    if doc.leave_application or doc.leave_type:
                        if second_half_done or first_half_done:
                            doc.half_day_status = "Present"
                        else:
                            doc.half_day_status = "Absent"
                    else:
                        doc.half_day_status = "Absent"                   
                else:
                    doc.half_day_status = None
        else:
            # Within quota → allow Present
            if status_lc == "half day":
                doc.status = "Present"
                if hasattr(doc, "half_day_status"):
                    doc.half_day_status = None
                if hasattr(doc, "custom_late_quota_override"):
                    doc.custom_late_quota_override = 1
    # ---------------------------------------------------------
    # FINAL EARLY EXIT ENFORCEMENT (APPLIED LAST)
    # ---------------------------------------------------------
    if cint(getattr(doc, "early_exit", 0) or 0):
        first_half_done = cint(getattr(doc, "custom_first_half_done", 0) or 0)
        if first_half_done:
            doc.status = "Half Day"
        else:
            doc.status = "Absent"
        if hasattr(doc, "half_day_status"):
            if doc.status == "Half Day":
                doc.half_day_status = "Absent"
            else:
                doc.half_day_status = None


# automatically advance the lock date when payroll is submitted,
#  which completely removes manual mistakes in Attendance Locked Upto
def lock_attendance_after_payroll(doc, method):
    pass
    # if not doc.end_date:
    #     return
    # payroll_end = getdate(doc.end_date)
    # settings = frappe.get_doc("VCM HR Settings")
    # current_lock = settings.attendance_locked_upto
    # # move lock forward only
    # if not current_lock or payroll_end > getdate(current_lock):
    #     settings.attendance_locked_upto = payroll_end
    #     settings.save(ignore_permissions=True)
    #     frappe.logger().info(
    #         f"[VCM] Attendance lock advanced to {payroll_end} after Payroll Entry {doc.name}"
    #     )