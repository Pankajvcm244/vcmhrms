frappe.pages['temp-punch-simulator'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({parent:wrapper, title:__('Temp Punch Simulator'), single_column:true});
    const esc = value => frappe.utils.escape_html(String(value ?? ''));
    const container = $('<div class="p-3"></div>').appendTo(page.main);
    container.append('<p>Select a worker and enter punch times directly. No IN/OUT is required. Submission creates real, immutable Temp Punch records marked as simulated. Use staging test workers.</p>');
    const entry=$('<div class="frappe-card p-3 mb-3"></div>').appendTo(container);
    entry.append('<h4>Add Punch Times</h4><p>Select one worker and add one row per punch across any dates. For night duty, the morning punch uses the next date. Add only one punch to test a missing counterpart.</p>');
    const worker=frappe.ui.form.make_control({parent:entry,df:{fieldname:'worker',label:'Temp Worker',fieldtype:'Link',options:'Temp Worker',reqd:1},render_input:true});
    const times=frappe.ui.form.make_control({parent:entry,df:{fieldname:'punch_times',label:'Punch Times � multiple dates allowed',fieldtype:'Table',in_place_edit:1,data:[],fields:[
        {fieldname:'punch_time',label:'Punch Date and Time',fieldtype:'Datetime',reqd:1,in_list_view:1}
    ]},render_input:true});
    const add=$('<button class="btn btn-default mb-3">Build Preview</button>').appendTo(entry);
    const submitButton=$('<button class="btn btn-primary mb-3 ml-2">Submit Test Punches</button>').appendTo(entry);
    const reset=$('<button class="btn btn-default mb-3 ml-2">Clear Preview</button>').appendTo(entry);
    const advanced=$('<details class="mb-3"><summary>Advanced: JSON upload / editor</summary></details>').appendTo(container);
    const controls = $('<div class="mb-3"></div>').appendTo(advanced);
    const upload = $('<input type="file" accept=".json,application/json" aria-label="Select punch JSON file">').appendTo(controls);
    const editor = frappe.ui.form.make_control({parent:advanced,df:{fieldname:'payload',label:'Punch JSON',fieldtype:'Code',options:'JSON'},render_input:true});
    editor.set_value(JSON.stringify({punches:[]},null,2));
    const finalize = frappe.ui.form.make_control({parent:container,df:{fieldname:'finalize',label:'Advance global sync completeness using synced_through (System Manager only)',fieldtype:'Check',default:0},render_input:true});
    container.append('<p class="text-muted">Leave this unchecked to test punches without finalizing attendance. Checked advances completeness for ALL locations; use only on an isolated staging site.</p>');
    const preview = $('<div style="overflow:auto"></div>').appendTo(container);
    const output = $('<div class="mt-3" style="overflow:auto"></div>').appendTo(container);
    let busy=false;
    function parse() {
        let payload;
        try { payload=JSON.parse(editor.get_value()); }
        catch (_) { frappe.throw(__('Invalid JSON.')); }
        if (!payload || !Array.isArray(payload.punches) || payload.punches.length<1 || payload.punches.length>500) {
            frappe.throw(__('JSON must contain between 1 and 500 punches.'));
        }
        for (const row of payload.punches) {
            if (!row || typeof row!=='object' || Array.isArray(row) || !row.event_id || !row.person_id || !row.punch_time) {
                frappe.throw(__('Each punch requires event_id, person_id and punch_time.'));
            }
        }
        return payload;
    }
    function renderPreview(payload) {
        preview.html(`<h5>${payload.punches.length} punches to submit</h5><table class="table table-bordered"><thead><tr><th>Event ID</th><th>SmartOffice ID</th><th>Timestamp</th><th>Device</th><th>Location</th></tr></thead><tbody>`+
            payload.punches.map(row=>'<tr>'+[row.event_id,row.person_id,row.punch_time,row.device_id,row.location].map(v=>`<td>${esc(v)}</td>`).join('')+'</tr>').join('')+'</tbody></table>');
    }
    let inputMode='table', builtSignature='';
    async function buildPreview() {
        if (!worker.get_value()) frappe.throw(__('Select one Temp Worker.'));
        const values=times.grid.get_data().map(row=>row.punch_time);
        if (!values.length || values.some(value=>!value)) frappe.throw(__('Add punch timestamps in the table.'));
        const signature=JSON.stringify([worker.get_value(),values]);
        if (inputMode==='table' && signature===builtSignature) return;
        const {message:sample}=await frappe.call({method:'vcmhrms.temp_worker.simulator.prepare_manual_punches',args:{worker:worker.get_value(),punch_times:JSON.stringify(values)}});
        editor.set_value(JSON.stringify(sample,null,2));
        renderPreview(sample);
        inputMode='table'; builtSignature=signature;
        output.empty();
    }
    add.on('click',async()=>{
        add.prop('disabled',true);
        try { builtSignature=''; await buildPreview(); }
        finally { add.prop('disabled',false); }
    });
    reset.on('click',()=>{
        editor.set_value(JSON.stringify({punches:[]},null,2));
        times.df.data=[]; times.grid.refresh();
        inputMode='table'; builtSignature='';
        preview.empty(); output.empty(); finalize.set_value(0);
    });
    upload.on('change',async function() {
        const file=this.files[0];
        if (!file) return;
        if (file.size>500000) return frappe.msgprint(__('Maximum file size is 500 KB.'));
        try {
            editor.set_value(await file.text());
            renderPreview(parse());
            inputMode='json'; builtSignature='';
            output.empty();
        } catch (error) { frappe.msgprint(__('Could not read valid punch JSON.')); }
        this.value='';
    });
    page.add_inner_button(__('Preview JSON'),()=>{ inputMode='json'; renderPreview(parse()); });
    page.add_inner_button(__('Download JSON'),async()=>{
        if (inputMode==='table') await buildPreview();
        const payload=parse();
        const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
        const anchor=document.createElement('a');
        anchor.href=url; anchor.download='temp-worker-sample-punches.json'; anchor.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    page.add_inner_button(__('Generate Sample'),()=>{
        if (!worker.get_value()) return frappe.msgprint('Select a worker first.');
        const dialog=new frappe.ui.Dialog({title:'Fill Sample Times',fields:[
            {fieldname:'date',label:'Duty Start Date (past date)',fieldtype:'Date',reqd:1,default:frappe.datetime.add_days(frappe.datetime.get_today(),-2)},
            {fieldname:'scenario',label:'Scenario',fieldtype:'Select',options:'Day\nNight\nMultiple Duties',default:'Day',reqd:1}
        ],primary_action_label:'Fill Punch Table',primary_action:values=>{
            const next=frappe.datetime.add_days(values.date,1);
            const examples={Day:[[values.date,'09:00:00'],[values.date,'18:00:00']],Night:[[values.date,'22:00:00'],[next,'06:00:00']],
                'Multiple Duties':[[values.date,'06:00:00'],[values.date,'10:00:00'],[values.date,'14:00:00'],[values.date,'18:00:00']]};
            times.df.data.push(...examples[values.scenario].map(([date,time])=>({punch_time:`${date} ${time}`})));
            times.grid.refresh();
            inputMode='table'; builtSignature='';
            editor.set_value(JSON.stringify({punches:[]},null,2));
            preview.empty(); output.empty(); finalize.set_value(0);
            dialog.hide();
        }});
        dialog.show();
    });
    async function submitPunches() {
        if (busy) return;
        if (!worker.get_value()) return frappe.msgprint('Select one Temp Worker.');
        busy=true;
        submitButton.prop('disabled',true);
        try {
            if (inputMode==='table') await buildPreview();
            const payload=parse();
            renderPreview(payload);
            const {message:result}=await frappe.call({method:'vcmhrms.temp_worker.simulator.submit_sample',args:{payload:JSON.stringify(payload),worker:worker.get_value(),finalize:finalize.get_value()?1:0},freeze:true,freeze_message:__('Submitting simulated punches…')});
            const counts={created:0,duplicate:0,failed:0};
            result.results.forEach(row=>{counts[row.status]=(counts[row.status]||0)+1;});
            output.html(`<h5>Created: ${counts.created} · Duplicates: ${counts.duplicate} · Failed: ${counts.failed}</h5><table class="table table-bordered"><thead><tr><th>Event / Row</th><th>Status</th><th>Message</th></tr></thead><tbody>`+
                result.results.map(row=>`<tr><td>${esc(row.event_id||row.row)}</td><td>${esc(row.status)}</td><td>${esc(row.message||'')}</td></tr>`).join('')+'</tbody></table><p>Attendance recalculation is queued. Open the roster and click Recalculate Attendance for immediate results.</p>');
        } finally {
            busy=false;
            submitButton.prop('disabled',false);
        }
    }
    submitButton.on('click',submitPunches);
    page.set_primary_action(__('Submit Test Punches'),submitPunches);
    page.add_inner_button(__('Punch Log'),()=>{frappe.route_options={is_simulated:1};frappe.set_route('List','Temp Punch');});
    page.add_inner_button(__('Monthly Roster'),()=>frappe.set_route('temp-monthly-roster'));
};

