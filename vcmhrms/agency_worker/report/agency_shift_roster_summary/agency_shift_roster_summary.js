// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt

frappe.query_reports['Agency Shift Roster Summary'] = {
    filters: [
        {
            fieldname: 'from_date', label: 'From Date', fieldtype: 'Date', reqd: 1,
            default: (() => {
                const today = frappe.datetime.get_today();
                const current = new Date(`${today}T00:00:00`);
                if (current.getDate() >= 26) return frappe.datetime.obj_to_str(new Date(current.getFullYear(), current.getMonth(), 26));
                return frappe.datetime.obj_to_str(new Date(current.getFullYear(), current.getMonth() - 1, 26));
            })()
        },
        {
            fieldname: 'to_date', label: 'To Date', fieldtype: 'Date', reqd: 1,
            default: (() => {
                const today = frappe.datetime.get_today();
                const current = new Date(`${today}T00:00:00`);
                const end = current.getDate() >= 26
                    ? new Date(current.getFullYear(), current.getMonth() + 1, 25)
                    : new Date(current.getFullYear(), current.getMonth(), 25);
                return frappe.datetime.obj_to_str(end);
            })()
        },
        {fieldname: 'budget_location', label: 'Budget Location', fieldtype: 'Link', options: 'Budget Location', default: 'VRN', reqd: 1},
        {fieldname: 'agency', label: 'Agency', fieldtype: 'Link', options: 'Agency'},
        {fieldname: 'department', label: 'Department', fieldtype: 'Select', options: `
HKM- Operation Housekeeping
TSF- Matchless Gift Shop
HKMV- Operation Security
TSF- Braj Nidhi Guest House
HKMV- Borewell
TSF- Annakoot
Pest Control
Maintenance
MK Maintenance
RO Maintenance
MK Security`},
        {fieldname: 'shift', label: 'Shift', fieldtype: 'Link', options: 'Agency Shift'}
    ]
};

