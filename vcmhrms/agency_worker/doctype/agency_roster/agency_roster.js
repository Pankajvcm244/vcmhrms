frappe.ui.form.on('Agency Roster', {
    refresh(frm) {
        if (frappe.user_roles.includes('System Manager') || frappe.user_roles.includes('ERP Manager')) {
            frm.add_custom_button(__('Punch Simulator'), () => frappe.set_route('ag-punch-simulator'));
        }
        if (frappe.user_roles.includes('System Manager') || frappe.user_roles.includes('Agency Worker Manager')) {
            frm.add_custom_button(__('Bulk OT Approval'), () => frappe.set_route('ag-ot-approval'));
            frm.add_custom_button(__('Attendance Review'), () => frappe.set_route('ag-attend-review-page'));
        }
        if (!frm.is_new()) frm.add_custom_button(__('Recalculate Attendance'), async () => {
            const result = await frappe.call({method: 'vcmhrms.agency_worker.service.recalculate_attendance', args: {roster: frm.doc.name}});
            frappe.set_route('Form', 'Agency Attendance', result.message);
        });
    }
});
frappe.ui.form.on('Agency Roster Duty', {
    async shift(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (!row.shift || !frm.doc.roster_date) return;
        const selectedShift = row.shift;
        const date = frm.doc.roster_date;
        const {message: shift} = await frappe.db.get_value('Agency Shift', selectedShift, ['start_time', 'end_time']);
        if (row.shift !== selectedShift || frm.doc.roster_date !== date) return;
        // Frappe Time fields can arrive as 9:00:00 rather than 09:00:00.
        // String ordering therefore cannot determine whether a shift crosses midnight.
        const normalize = value => {
            const parts = String(value).split(':').map(Number);
            const [hour, minute, second = 0] = parts;
            if (parts.length < 2 || parts.length > 3 || !Number.isInteger(hour) || !Number.isInteger(minute)
                || !Number.isFinite(second) || hour < 0 || hour > 23 || minute < 0 || minute > 59
                || second < 0 || second >= 60) {
                frappe.throw(__('Invalid shift time. Check Start Time and End Time on the shift.'));
            }
            return {
                seconds: hour * 3600 + minute * 60 + second,
                text: [hour, minute, Math.floor(second)].map(part => String(part).padStart(2, '0')).join(':')
            };
        };
        const start = normalize(shift.start_time), end = normalize(shift.end_time);
        const endDate = end.seconds <= start.seconds ? frappe.datetime.add_days(date, 1) : date;
        await frappe.model.set_value(cdt, cdn, 'start_datetime', `${date} ${start.text}`);
        await frappe.model.set_value(cdt, cdn, 'end_datetime', `${endDate} ${end.text}`);
    }
});
