frappe.query_reports['Temp Monthly Attendance'] = {
    onload(report) {
        report.page.add_inner_button(__('Attendance Review'),()=>frappe.set_route('attend-review-page'));
        report.page.add_inner_button(__('OT Approval'),()=>frappe.set_route('temp-ot-approval'));
    },
    filters: [
        {fieldname:'month',label:'Month (MM-YYYY)',fieldtype:'Data',reqd:1,
            default:(()=>{const value=frappe.datetime.get_today(); return `${value.slice(5,7)}-${value.slice(0,4)}`;})()},
        {fieldname:'branch',label:'Branch',fieldtype:'Link',options:'Branch',default:'Vrindavan'},
        {fieldname:'manager',label:'Manager (blank = All)',fieldtype:'Link',options:'User',
            default:frappe.user_roles.includes('System Manager')?'':frappe.session.user,
            get_query:()=>({query:'vcmhrms.temp_worker.report.temp_monthly_attendance.temp_monthly_attendance.get_supervisors',
                filters:{branch:frappe.query_report.get_filter_value('branch')}})}
    ],
    formatter(value,row,column,data,default_formatter) {
        value=default_formatter(value,row,column,data);
        if (!column.fieldname.startsWith('day_')) return value;
        const raw=String(data[column.fieldname] || '');
        if (raw.startsWith('A') || raw.startsWith('NR')) return `<span style="color:#c0392b;font-weight:600">${value}</span>`;
        if (raw.startsWith('HD') || raw.startsWith('PD') || raw.includes('!')) return `<span style="color:#b7791f;font-weight:600">${value}</span>`;
        if (raw.startsWith('P')) return `<span style="color:#26734d">${value}</span>`;
        return value;
    }
};
