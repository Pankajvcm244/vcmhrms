frappe.pages['temp-monthly-roster'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: __('Temp Monthly Roster'), single_column: true});
    const today=frappe.datetime.get_today();
    const month = page.add_field({fieldname:'month',label:'Month (MM-YYYY)',fieldtype:'Data',default:`${today.slice(5,7)}-${today.slice(0,4)}`});
    const branch = page.add_field({fieldname:'branch',label:'Branch',fieldtype:'Link',options:'Branch',default:'Vrindavan'});
    const body = $('<div style="overflow:auto"></div>').appendTo(page.main);
    const esc = value => frappe.utils.escape_html(String(value || ''));
    let loadedData=null;
    let loadedMonth=null;
    function selectedWorkers(){
        return body.find('.worker-select:checked').map((_,element)=>element.dataset.worker).get();
    }
    page.add_inner_button(__('Bulk Assign Simple Shift'),()=>{
        const workers=selectedWorkers();
        if(!loadedData) return frappe.msgprint(__('Load the roster first.'));
        if(!workers.length) return frappe.msgprint(__('Select at least one worker from the first column.'));
        const [monthNumber,year]=loadedMonth.split('-');
        const lastDay=new Date(Number(year),Number(monthNumber),0).getDate();
        const dialog=new frappe.ui.Dialog({title:__('Bulk Assign Simple Shift'),fields:[
            {fieldname:'selected',fieldtype:'HTML',options:`<p><b>${workers.length}</b> workers selected. Existing dates are skipped by default so OT and multiple-shift exceptions remain unchanged.</p>`},
            {fieldname:'from_date',label:'From Date',fieldtype:'Date',reqd:1,default:`${year}-${monthNumber}-01`},
            {fieldname:'to_date',label:'To Date',fieldtype:'Date',reqd:1,default:`${year}-${monthNumber}-${String(lastDay).padStart(2,'0')}`},
            {fieldname:'shift',label:'Simple Shift',fieldtype:'Link',options:'Temp Shift',reqd:1},
            {fieldname:'weekly_off',label:'Weekly Off Day',fieldtype:'Select',options:'\nMonday\nTuesday\nWednesday\nThursday\nFriday\nSaturday\nSunday'},
            {fieldname:'existing_action',label:'Existing Roster Dates',fieldtype:'Select',options:'Skip Existing\nReplace Existing',default:'Skip Existing',reqd:1},
            {fieldname:'warning',fieldtype:'HTML',options:'<p class="text-muted">Use Replace Existing only when you intend to remove existing OT or multiple duties in this date range.</p>'}
        ],primary_action_label:__('Apply Assignment'),primary_action:async values=>{
            if(values.existing_action==='Replace Existing') {
                const confirmed=await new Promise(resolve=>frappe.confirm(
                    __('Replace existing roster dates for the selected workers? Existing OT and multiple duties in the range will be removed.'),
                    ()=>resolve(true),()=>resolve(false)));
                if(!confirmed) return;
            }
            const {message}=await frappe.call({method:'vcmhrms.temp_worker.roster.allocate_bulk',type:'POST',args:{
                workers,branch:branch.get_value(),...values},freeze:true});
            dialog.hide();
            if(message.mode==='completed') {
                const result=message.result;
                frappe.msgprint(__(`Roster assignment completed: ${result.created} created, ${result.replaced} replaced, ${result.skipped} skipped, ${result.failed_workers} failed.`));
                page.btn_primary.trigger('click');
            } else {
                const job=message.job_id?` Job: ${message.job_id}`:'';
                frappe.show_alert({message:__(`${message.queued} workers queued on the default background queue.${job}`),indicator:'green'},15);
            }
        }});
        dialog.show();
    });
    page.set_primary_action(__('Load Roster'), async () => {
        if (!/^\d{2}-\d{4}$/.test(month.get_value()) || !branch.get_value()) return frappe.msgprint(__('Select a branch and enter MM-YYYY.'));
        const selectedMonth = month.get_value(), selectedBranch = branch.get_value();
        const [selectedMonthNumber,selectedYear]=selectedMonth.split('-');
        const {message: data} = await frappe.call({method:'vcmhrms.temp_worker.roster.get_month',args:{month:selectedMonth,branch:selectedBranch}});
        loadedData=data;
        loadedMonth=selectedMonth;
        const lookup = new Map(data.rosters.map(r => [`${r.worker}|${String(r.roster_date).slice(0,10)}`,r]));
        let html = '<p>Select workers for a simple bulk shift. Click a day to edit exceptions such as OT or multiple shifts. OFF = weekly off.</p><table class="table table-bordered"><thead><tr><th><input type="checkbox" class="select-all-workers" title="Select all"></th><th>Worker Name</th><th>Staff ID</th><th>Supervisor</th>';
        for (let day=1; day<=data.days; day++) html += `<th>${day}</th>`;
        html += '</tr></thead><tbody>';
        data.workers.forEach((worker,index) => {
            html += `<tr><td><input type="checkbox" class="worker-select" data-worker="${esc(worker.name)}"></td><th style="white-space:nowrap">${esc(worker.worker_name)}</th><td>${esc(worker.staff_id)}</td><td>${esc(worker.supervisor)}</td>`;
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
        body.find('.select-all-workers').on('change',function(){body.find('.worker-select').prop('checked',this.checked);});
        body.find('button[data-worker]').on('click',function() {
            const worker=data.workers[Number(this.dataset.worker)], date=this.dataset.date;
            const row=lookup.get(`${worker.name}|${date}`);
            if(row) frappe.set_route('Form','Temp Roster',row.name);
            else frappe.new_doc('Temp Roster',{worker:worker.name,roster_date:date,branch:selectedBranch,day_type:'Duty'});
        });
    });
    frappe.realtime.on('temp_roster_bulk_complete',message=>{
        frappe.msgprint(__(`Bulk roster completed: ${message.created} created, ${message.replaced} replaced, ${message.skipped} skipped, ${message.failed_workers} workers failed.`));
    });
};
