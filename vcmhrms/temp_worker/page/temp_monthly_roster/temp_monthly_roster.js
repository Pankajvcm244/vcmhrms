frappe.pages['temp-monthly-roster'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: __('Temp Monthly Roster'), single_column: true});
    const today=frappe.datetime.get_today();
    const month = page.add_field({fieldname:'month',label:'Month (MM-YYYY)',fieldtype:'Data',default:`${today.slice(5,7)}-${today.slice(0,4)}`});
    const branch = page.add_field({fieldname:'branch',label:'Branch',fieldtype:'Link',options:'Branch',default:'Vrindavan'});
    const body = $('<div style="overflow:auto"></div>').appendTo(page.main);
    const esc = value => frappe.utils.escape_html(String(value || ''));
    page.set_secondary_action(__('Allocate Date Range'), () => {
        const dialog = new frappe.ui.Dialog({title:'Allocate Date Range',fields:[
            {fieldname:'worker',label:'Worker',fieldtype:'Link',options:'Temp Worker',reqd:1},
            {fieldname:'branch',label:'Branch',fieldtype:'Link',options:'Branch',reqd:1,default:branch.get_value()},
            {fieldname:'from_date',label:'From Date',fieldtype:'Date',reqd:1},
            {fieldname:'to_date',label:'To Date',fieldtype:'Date',reqd:1},
            {fieldname:'day_type',label:'Duty / Off',fieldtype:'Select',options:'Duty\nWeekly Off',default:'Duty',reqd:1},
            {fieldname:'shift',label:'Shift',fieldtype:'Link',options:'Temp Shift',mandatory_depends_on:'eval:doc.day_type=="Duty"'},
            {fieldname:'planned_before_ot_hours',label:'Planned Before-Shift OT',fieldtype:'Float',default:0},
            {fieldname:'planned_after_ot_hours',label:'Planned After-Shift OT',fieldtype:'Float',default:0},
            {fieldname:'ot_reason',label:'OT Reason',fieldtype:'Small Text'},
            {fieldname:'replace',label:'Replace existing allocations in this range',fieldtype:'Check',reqd:1}
        ],primary_action_label:'Apply Allocations',primary_action:async values => {
            if (!values.replace) return frappe.msgprint('Confirm replacement of allocations in the selected range.');
            delete values.replace;
            await frappe.call({method:'vcmhrms.temp_worker.roster.allocate_range',args:values,freeze:true});
            dialog.hide();
            frappe.show_alert('Allocations saved. Load the roster to refresh.');
        }});
        dialog.show();
    });
    page.set_primary_action(__('Load Roster'), async () => {
        if (!/^\d{2}-\d{4}$/.test(month.get_value()) || !branch.get_value()) return frappe.msgprint(__('Select a branch and enter MM-YYYY.'));
        const selectedMonth = month.get_value(), selectedBranch = branch.get_value();
        const [selectedMonthNumber,selectedYear]=selectedMonth.split('-');
        const {message: data} = await frappe.call({method:'vcmhrms.temp_worker.roster.get_month',args:{month:selectedMonth,branch:selectedBranch}});
        const lookup = new Map(data.rosters.map(r => [`${r.worker}|${String(r.roster_date).slice(0,10)}`,r]));
        let html = '<p>Click a day to edit duties. OFF = weekly off. Multiple shifts appear together.</p><table class="table table-bordered"><thead><tr><th>Worker Name</th><th>Staff ID</th><th>Supervisor</th>';
        for (let day=1; day<=data.days; day++) html += `<th>${day}</th>`;
        html += '</tr></thead><tbody>';
        data.workers.forEach((worker,index) => {
            html += `<tr><th style="white-space:nowrap">${esc(worker.worker_name)}</th><td>${esc(worker.staff_id)}</td><td>${esc(worker.supervisor)}</td>`;
            for(let day=1;day<=data.days;day++) {
                const date = `${selectedYear}-${selectedMonthNumber}-${String(day).padStart(2,'0')}`;
                const row=lookup.get(`${worker.name}|${date}`);
                const label=row ? (row.day_type==='Weekly Off'?'OFF':row.shifts.join(' + ')) : '+';
                html += `<td><button class="btn btn-xs ${label==='OFF'?'btn-success':'btn-default'}" data-worker="${index}" data-date="${date}">${esc(label)}</button></td>`;
            }
            html += '</tr>';
        });
        html += '</tbody></table>';
        body.html(html);
        body.find('button[data-worker]').on('click',function() {
            const worker=data.workers[Number(this.dataset.worker)], date=this.dataset.date;
            const row=lookup.get(`${worker.name}|${date}`);
            if(row) frappe.set_route('Form','Temp Roster',row.name);
            else frappe.new_doc('Temp Roster',{worker:worker.name,roster_date:date,branch:selectedBranch,day_type:'Duty'});
        });
    });
};
