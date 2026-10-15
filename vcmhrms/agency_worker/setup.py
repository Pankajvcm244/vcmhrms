import frappe


def after_migrate():
    """Create only the roles and indexes required by a fresh Agency installation."""
    for role in ['Agency Worker Manager','Agency Roster Manager','Agency Worker Supervisor','Agency Worker Integration','ERP Manager']:
        if not frappe.db.exists('Role',role):
            frappe.get_doc(dict(doctype='Role',role_name=role,desk_access=int(role!='Agency Worker Integration'))).insert(ignore_permissions=True)
    frappe.db.add_unique('Agency Roster',['worker','roster_date'],constraint_name='unique_agency_worker_date')

