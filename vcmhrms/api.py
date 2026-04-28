# Copyright (c) 2026, Pankaj Sharma
# For license information, please see license.txt


"""
curl -X POST "https://pankaj.vcmerp.in/api/method/vcmhrms.api.add_checkin" \
  -H "Authorization: token 264efb5834bb105:8f7b377b3d319a0" \
  -H "Content-Type: application/json" \
  -d '{
    "employee_id": "HP1671",
    "punch_datetime": "2026-04-27 09:34:12"
  }'
"""
import frappe
from frappe.utils import get_datetime, now_datetime


@frappe.whitelist(methods=["POST"])
def add_checkin():
    """
    API endpoint for third-party biometric systems.
    URL:
        /api/method/vcmhrms.api.add_checkin
    Auth:
        Authorization: token api_key:api_secret
    Single Payload:
        {
            "employee_id": "HP1671",
            "punch_datetime": "2026-04-27 09:34:12"
        }
    Batch Payload:
        [
            {
                "employee_id": "HP1671",
                "punch_datetime": "2026-04-27 09:34:12"
            }
        ]
    """
    _ensure_biometric_api_user()
    data = frappe.request.get_json() or {}
    if isinstance(data, list):
        return _process_batch(data)
    return _process_single(data)


def _ensure_biometric_api_user():
    """
    Only users with this role can push biometric checkins.
    """
    if frappe.session.user == "Guest":
        frappe.throw("Authentication required", frappe.AuthenticationError)
    if "HR User" not in frappe.get_roles(frappe.session.user):
        frappe.throw(
            "User is not allowed to push biometric checkins",
            frappe.PermissionError
        )


def _process_batch(rows):
    result = {
        "success": True,
        "total": len(rows),
        "created": 0,
        "duplicates": 0,
        "failed": 0,
        "rows": []
    }
    for idx, row in enumerate(rows, start=1):
        try:
            single_result = _process_single(row)
            result["rows"].append({
                "row": idx,
                **single_result
            })
            if single_result.get("status") == "created":
                result["created"] += 1
            elif single_result.get("status") == "duplicate":
                result["duplicates"] += 1
        except Exception as e:
            frappe.log_error(
                title="Biometric API Checkin Failed",
                message=frappe.get_traceback()
            )
            result["failed"] += 1
            result["rows"].append({
                "row": idx,
                "status": "failed",
                "message": str(e)
            })
    if result["failed"]:
        result["success"] = False
    return result


def _process_single(data):
    employee_id = (data.get("employee_id") or "").strip()
    punch_datetime = data.get("punch_datetime")
    if not employee_id:
        frappe.throw("employee_id is required")
    if not punch_datetime:
        frappe.throw("punch_datetime is required")
    checkin_time = _parse_punch_datetime(punch_datetime)
    employee = _get_employee(employee_id)
    duplicate_name = _find_duplicate_checkin(
        employee=employee,
        checkin_time=checkin_time
    )
    if duplicate_name:
        return {
            "status": "duplicate",
            "message": "Checkin already exists",
            "employee": employee,
            "employee_checkin": duplicate_name
        }
    doc = frappe.get_doc({
        "doctype": "Employee Checkin",
        "employee": employee,
        "time": checkin_time,
        "skip_auto_attendance": 0
    })
    doc.insert(ignore_permissions=True)
    return {
        "status": "created",
        "message": "Checkin created successfully",
        "employee": employee,
        "employee_checkin": doc.name
    }


def _parse_punch_datetime(punch_datetime):
    try:
        checkin_time = get_datetime(punch_datetime)
    except Exception:
        frappe.throw("Invalid punch_datetime format. Use YYYY-MM-DD HH:mm:ss")
    if checkin_time > now_datetime():
        frappe.throw("Future punch_datetime is not allowed")
    return checkin_time


def _get_employee(employee_id):
    """
    First match Employee.name.
    Fallback to Employee.attendance_device_id.
    """
    employee = frappe.db.get_value(
        "Employee",
        {
            "name": employee_id,
            "status": "Active"
        },
        "name"
    )
    if not employee:
        employee = frappe.db.get_value(
            "Employee",
            {
                "attendance_device_id": employee_id,
                "status": "Active"
            },
            "name"
        )
    if not employee:
        frappe.throw(f"No active employee found for employee_id: {employee_id}")
    return employee


def _find_duplicate_checkin(employee, checkin_time):
    return frappe.db.get_value(
        "Employee Checkin",
        {
            "employee": employee,
            "time": checkin_time
        },
        "name"
    )