import frappe


def budget_locations(user):
    return frappe.get_all('User Permission',filters={'user':user,'allow':'Budget Location'},pluck='for_value')


def unrestricted(user):
    return user=='Administrator' or 'System Manager' in frappe.get_roles(user)


def _assigned(worker, child_doctype, user=None):
    user=user or frappe.session.user
    if unrestricted(user): return True
    return bool(frappe.db.exists(child_doctype, {'parent':worker, 'parenttype':'Agency Worker', 'user':user}))


def is_roster_manager(worker,user=None):
    return _assigned(worker,'Agency Worker Roster Manager',user)


def is_supervisor(worker,user=None):
    return _assigned(worker,'Agency Worker Supervisor',user)


def require_roster_manager(worker):
    if not is_roster_manager(worker):
        frappe.throw('Only a roster manager assigned to this agency staff member can update the roster.',frappe.PermissionError)


def require_supervisor(worker):
    if not is_supervisor(worker):
        frappe.throw('Only a supervisor assigned to this agency staff member can review attendance or OT.',frappe.PermissionError)


def query(user=None, doctype=None):
    user=user or frappe.session.user
    if unrestricted(user): return ''
    allowed=budget_locations(user)
    if not allowed: return '1=0'
    values=','.join(frappe.db.escape(b) for b in allowed)
    if doctype in ('Agency Worker','Agency Roster'):
        return f'`tab{doctype}`.budget_location in ({values})'
    if doctype=='Agency Attendance':
        return f'`tabAgency Attendance`.roster in (select name from `tabAgency Roster` where budget_location in ({values}))'
    return f'`tabAgency Punch`.worker in (select name from `tabAgency Worker` where budget_location in ({values}))'


def worker_query(user=None): return query(user,'Agency Worker')
def roster_query(user=None): return query(user,'Agency Roster')
def attendance_query(user=None): return query(user,'Agency Attendance')
def punch_query(user=None): return query(user,'Agency Punch')


def has_permission(doc,user=None,permission_type=None,**kwargs):
    user=user or frappe.session.user
    if unrestricted(user): return None
    if doc.doctype in ('Agency Worker','Agency Roster'):
        budget_location=doc.budget_location
    elif doc.doctype=='Agency Attendance':
        budget_location=frappe.db.get_value('Agency Roster',doc.roster,'budget_location')
    else:
        budget_location=frappe.db.get_value('Agency Worker',doc.worker,'budget_location')
    if budget_location not in budget_locations(user): return False
    roles=set(frappe.get_roles(user))
    worker=doc.name if doc.doctype=='Agency Worker' else getattr(doc,'worker',None)
    if worker and permission_type in ('write','create','delete','submit','cancel'):
        if doc.doctype=='Agency Roster' and 'Agency Roster Manager' in roles:
            return is_roster_manager(worker,user)
        if doc.doctype=='Agency Attendance' and 'Agency Worker Supervisor' in roles:
            return is_supervisor(worker,user)
    return None

