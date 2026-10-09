import frappe


def branches(user):
    return frappe.get_all('User Permission',filters={'user':user,'allow':'Branch'},pluck='for_value')


def unrestricted(user):
    return user=='Administrator' or 'System Manager' in frappe.get_roles(user)


def query(user=None, doctype=None):
    user=user or frappe.session.user
    if unrestricted(user): return ''
    allowed=branches(user)
    if not allowed: return '1=0'
    values=','.join(frappe.db.escape(b) for b in allowed)
    if doctype in ('Temp Worker','Temp Roster'):
        return f'`tab{doctype}`.branch in ({values})'
    if doctype=='Temp Attendance':
        return f'`tabTemp Attendance`.roster in (select name from `tabTemp Roster` where branch in ({values}))'
    return f'`tabTemp Punch`.worker in (select name from `tabTemp Worker` where branch in ({values}))'


def worker_query(user=None): return query(user,'Temp Worker')
def roster_query(user=None): return query(user,'Temp Roster')
def attendance_query(user=None): return query(user,'Temp Attendance')
def punch_query(user=None): return query(user,'Temp Punch')


def has_permission(doc,user=None,permission_type=None,**kwargs):
    user=user or frappe.session.user
    if unrestricted(user): return None
    if doc.doctype in ('Temp Worker','Temp Roster'):
        branch=doc.branch
    elif doc.doctype=='Temp Attendance':
        branch=frappe.db.get_value('Temp Roster',doc.roster,'branch')
    else:
        branch=frappe.db.get_value('Temp Worker',doc.worker,'branch')
    if branch not in branches(user): return False
    return None
