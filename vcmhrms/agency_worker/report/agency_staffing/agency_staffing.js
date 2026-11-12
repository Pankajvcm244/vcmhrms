// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt

function agency_staffing_cycles() {
    const today=new Date(`${frappe.datetime.get_today()}T00:00:00`);
    const endMonth=new Date(today.getFullYear(),today.getMonth()+(today.getDate()>=26?1:0),1);
    return [0,1,2].map(offset=>{
        const end=new Date(endMonth.getFullYear(),endMonth.getMonth()-offset,25);
        const start=new Date(end.getFullYear(),end.getMonth()-1,26);
        const value=`${end.getFullYear()}-${String(end.getMonth()+1).padStart(2,'0')}`;
        const label=`${String(end.getMonth()+1).padStart(2,'0')}-${end.getFullYear()} (${frappe.datetime.obj_to_str(start)} to ${frappe.datetime.obj_to_str(end)})`;
        return {value,label};
    });
}

const agencyStaffingCycles=agency_staffing_cycles();
frappe.query_reports['Agency Staffing']={
    filters:[
        {fieldname:'payroll_cycle',label:'Payroll Cycle',fieldtype:'Select',reqd:1,
            options:agencyStaffingCycles.map(row=>row.value).join('\n'),
            default:agencyStaffingCycles[0].value},
        {fieldname:'budget_location',label:'Budget Location',fieldtype:'Link',options:'Budget Location',default:'VRN',reqd:1},
        {fieldname:'agency',label:'Agency',fieldtype:'Link',options:'Agency'},
        {fieldname:'department',label:'Department',fieldtype:'Select',options:`
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
        {fieldname:'worker',label:'Agency Employee',fieldtype:'Link',options:'Agency Worker'}
    ]
};
