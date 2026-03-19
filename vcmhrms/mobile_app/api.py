

import frappe
from frappe.utils import nowdate
from hrms.hr.doctype.leave_application.leave_application import get_leave_balance_on
from hrms.hr.doctype.leave_application.leave_application import get_leave_allocation_records
from hrms.hr.doctype.leave_application.leave_application import get_leaves_for_period
from hrms.hr.doctype.leave_application.leave_application import get_manually_expired_leaves

from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee as core_get_holiday_list


from frappe.utils import flt
from frappe.utils import nowdate
from frappe.utils import cint



@frappe.whitelist(allow_guest=True)
def get_field_options():
    doctype = "Attendance Request"
    
    """Fetches the options of a specific DocField from any Doctype."""
    try:
        meta = frappe.get_meta(doctype)  # Get/fetches the metadata about the "Selected" Doctype.
        field = meta.get_field("reason")  # Get the specific field details

        if field:
            if field.fieldtype == "Select":
                options = field.options.split("\n")  # Convert options to a list
                return options


            return {"message": "Field does not have selectable options"}

        return {"error": "Field not found"}

    except Exception as e:
        return {"error":str(e)}



@frappe.whitelist()
def get_all_leave_balances(employee):
    leave_types = frappe.get_all("Leave Type", pluck="name")

    balances = []

    for lt in leave_types:

        # Get Actual Balance (Approved considered)
        balance = get_leave_balance_on(
            employee=employee,
            leave_type=lt,
            date=nowdate(),
            consider_all_leaves_in_the_allocation_period=True,
        )

        if not balance:
            continue

        balance = float(balance)

        # Get Pending Leave Days
        pending_days = frappe.db.sql("""
            SELECT SUM(total_leave_days)
            FROM `tabLeave Application`
            WHERE employee = %s
            AND leave_type = %s
            AND status = 'Open'
        """, (employee, lt))[0][0] or 0

        pending_days = float(pending_days)

        # Final Available Balance
        final_balance = balance - pending_days

        if final_balance > 0:
            balances.append({
                "leave_type": lt,
                "balance": final_balance
            })

    return balances




@frappe.whitelist()
def get_employee_holiday_list(employee):
    """
    Wrapper method to fetch holiday list for employee
    Safe for Flutter API usage
    """
    if not employee:
        frappe.throw("Employee is required")

    holiday_list = core_get_holiday_list(employee)

    return holiday_list