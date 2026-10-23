import frappe


def unrestricted(user):
    return user=='Administrator' or 'System Manager' in frappe.get_roles(user)

def can_view_all_workers(user):
    roles=set(frappe.get_roles(user))
    return unrestricted(user) or bool({'Agency Worker Manager','ERP Manager'} & roles)


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
    if can_view_all_workers(user): return ''
    roles=set(frappe.get_roles(user))
    assignment_conditions=[]
    escaped_user=frappe.db.escape(user)
    if 'Agency Roster Manager' in roles:
        assignment_conditions.append(
            "exists(select 1 from `tabAgency Worker Roster Manager` rm "
            "where rm.parent=`tabAgency Worker`.name and rm.parenttype='Agency Worker' "
            f"and rm.user={escaped_user})")
    if 'Agency Worker Supervisor' in roles:
        assignment_conditions.append(
            "exists(select 1 from `tabAgency Worker Supervisor` ws "
            "where ws.parent=`tabAgency Worker`.name and ws.parenttype='Agency Worker' "
            f"and ws.user={escaped_user})")
    if not assignment_conditions: return '1=0'
    worker_scope='select name from `tabAgency Worker` where ('+ ' or '.join(assignment_conditions) +')'
    if doctype=='Agency Worker':
        return f'`tabAgency Worker`.name in ({worker_scope})'
    if doctype=='Agency Roster':
        return f'`tabAgency Roster`.worker in ({worker_scope})'
    if doctype=='Agency Attendance':
        return f'`tabAgency Attendance`.roster in (select name from `tabAgency Roster` where worker in ({worker_scope}))'
    return f'`tabAgency Punch`.worker in ({worker_scope})'


def worker_query(user=None): return query(user,'Agency Worker')
def roster_query(user=None): return query(user,'Agency Roster')
def attendance_query(user=None): return query(user,'Agency Attendance')
def punch_query(user=None): return query(user,'Agency Punch')


def has_permission(doc,user=None,permission_type=None,**kwargs):
    user=user or frappe.session.user
    if unrestricted(user): return None
    roles=set(frappe.get_roles(user))
    if doc.doctype=='Agency Worker' and {'Agency Worker Manager','ERP Manager'} & roles:
        return None
    worker=doc.name if doc.doctype=='Agency Worker' else getattr(doc,'worker',None)
    if worker and permission_type in (None,'read','select','report','export','print'):
        if 'Agency Roster Manager' in roles and is_roster_manager(worker,user): return None
        if 'Agency Worker Supervisor' in roles and is_supervisor(worker,user): return None
        return False
    if worker and permission_type in ('write','create','delete','submit','cancel'):
        if doc.doctype=='Agency Roster' and 'Agency Roster Manager' in roles:
            return is_roster_manager(worker,user)
        if doc.doctype=='Agency Attendance' and 'Agency Worker Supervisor' in roles:
            return is_supervisor(worker,user)
    return None
