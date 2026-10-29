// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt

frappe.pages['ag-monthly-roster'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __('Agency Monthly Roster'),
        single_column: true
    });
    const esc = value => frappe.utils.escape_html(String(value || ''));
    const today = frappe.datetime.get_today();

    function payrollMonth(dateText) {
        const current = new Date(`${dateText}T00:00:00`);
        if (current.getDate() >= 26) current.setMonth(current.getMonth() + 1);
        return `${String(current.getMonth() + 1).padStart(2, '0')}-${current.getFullYear()}`;
    }

    function monthOptions(defaultMonth) {
        const [defaultNumber, defaultYear] = defaultMonth.split('-').map(Number);
        const options = [];
        for (let offset = -12; offset <= 12; offset++) {
            const value = new Date(defaultYear, defaultNumber - 1 + offset, 1);
            options.push(`${String(value.getMonth() + 1).padStart(2, '0')}-${value.getFullYear()}`);
        }
        return options.join('\n');
    }

    const defaultMonth = payrollMonth(today);
    const month = page.add_field({
        fieldname: 'month',
        label: 'Payroll Month',
        fieldtype: 'Select',
        options: monthOptions(defaultMonth),
        default: defaultMonth,
        reqd: 1,
        change: () => {
            selectedWorkerNames.clear();
            loadRoster();
        }
    });
    const budget_location = page.add_field({
        fieldname: 'budget_location',
        label: 'Budget Location',
        fieldtype: 'Link',
        options: 'Budget Location',
        default: 'VRN',
        reqd: 1,
        change: () => {
            selectedWorkerNames.clear();
            loadRoster();
        }
    });
    const agency = page.add_field({
        fieldname: 'agency',
        label: 'Agency',
        fieldtype: 'Link',
        options: 'Agency',
        change: () => loadRoster()
    });
    const workerSearch = page.add_field({
        fieldname: 'worker_search',
        label: 'Search Staff Name / ID',
        fieldtype: 'Data',
        change: () => applyWorkerFilter()
    });

    const selectionBar = $(
        '<div class="mb-2 text-muted roster-selection-summary"></div>'
    ).appendTo(page.main);
    const body = $('<div style="overflow:auto"></div>').appendTo(page.main);
    const selectedWorkerNames = new Set();
    let loadedData = null;
    let loadedMonth = null;
    let loadRequest = 0;

    function updateSelectionSummary() {
        const visibleCount = body.find('tbody tr.worker-row:visible').length;
        selectionBar.html(
            `<b>${selectedWorkerNames.size}</b> ${__('staff selected')} &nbsp;·&nbsp; ` +
            `${visibleCount} ${__('shown')}` +
            (selectedWorkerNames.size
                ? ` &nbsp;<button class="btn btn-xs btn-default clear-worker-selection">${__('Clear selection')}</button>`
                : '')
        );
        selectionBar.find('.clear-worker-selection').on('click', () => {
            selectedWorkerNames.clear();
            body.find('.worker-select, .select-all-workers').prop('checked', false);
            updateSelectionSummary();
        });
    }

    function applyWorkerFilter() {
        const query = String(workerSearch.get_value() || '').trim().toLowerCase();
        body.find('tbody tr.worker-row').each(function() {
            const searchable = String(this.dataset.search || '').toLowerCase();
            $(this).toggle(!query || searchable.includes(query));
        });
        const visibleChecks = body.find('tbody tr.worker-row:visible .worker-select');
        const allVisibleSelected = visibleChecks.length && visibleChecks.toArray().every(
            checkbox => checkbox.checked
        );
        body.find('.select-all-workers').prop('checked', Boolean(allVisibleSelected));
        updateSelectionSummary();
    }

    function selectedWorkers() {
        return Array.from(selectedWorkerNames);
    }

    page.add_inner_button(__('Bulk Assign Simple Shift'), () => {
        const workers = selectedWorkers();
        if (!loadedData) return frappe.msgprint(__('Please wait while the roster loads.'));
        if (!workers.length) {
            return frappe.msgprint(__('Search staff and select at least one worker from the first column.'));
        }
        const periodStart = String(loadedData.period_start).slice(0, 10);
        const periodEnd = String(loadedData.period_end).slice(0, 10);
        const dialog = new frappe.ui.Dialog({
            title: __('Bulk Assign Simple Shift'),
            fields: [
                {
                    fieldname: 'selected',
                    fieldtype: 'HTML',
                    options: `<p><b>${workers.length}</b> staff selected. Existing dates are skipped by default so OT and multiple-shift exceptions remain unchanged.</p>`
                },
                {fieldname: 'from_date', label: 'From Date', fieldtype: 'Date', reqd: 1, default: periodStart},
                {fieldname: 'to_date', label: 'To Date', fieldtype: 'Date', reqd: 1, default: periodEnd},
                {fieldname: 'shift', label: 'Simple Shift', fieldtype: 'Link', options: 'Agency Shift', reqd: 1},
                {
                    fieldname: 'weekly_off',
                    label: 'Weekly Off Day',
                    fieldtype: 'Select',
                    options: 'Use Worker Default\nNo Weekly Off\nMonday\nTuesday\nWednesday\nThursday\nFriday\nSaturday\nSunday',
                    default: 'Use Worker Default'
                },
                {
                    fieldname: 'existing_action',
                    label: 'Existing Roster Dates',
                    fieldtype: 'Select',
                    options: 'Skip Existing\nReplace Existing',
                    default: 'Skip Existing',
                    reqd: 1
                },
                {
                    fieldname: 'warning',
                    fieldtype: 'HTML',
                    options: '<p class="text-muted">Use Replace Existing only when you intend to remove existing OT or multiple duties in this date range.</p>'
                }
            ],
            primary_action_label: __('Apply Assignment'),
            primary_action: async values => {
                if (values.existing_action === 'Replace Existing') {
                    const confirmed = await new Promise(resolve => frappe.confirm(
                        __('Replace existing roster dates for the selected staff? Existing OT and multiple duties in the range will be removed.'),
                        () => resolve(true),
                        () => resolve(false)
                    ));
                    if (!confirmed) return;
                }
                const {message} = await frappe.call({
                    method: 'vcmhrms.agency_worker.roster.allocate_bulk',
                    type: 'POST',
                    args: {workers, budget_location: budget_location.get_value(), ...values},
                    freeze: true
                });
                dialog.hide();
                if (message.mode === 'completed') {
                    const result = message.result;
                    frappe.msgprint(__(`Roster assignment completed: ${result.created} created, ${result.replaced} replaced, ${result.skipped} skipped, ${result.failed_workers} failed.`));
                    loadRoster();
                } else {
                    const job = message.job_id ? ` Job: ${message.job_id}` : '';
                    frappe.show_alert({
                        message: __(`${message.queued} staff queued on the default background queue.${job}`),
                        indicator: 'green'
                    }, 15);
                }
            }
        });
        dialog.show();
    });

    async function loadRoster() {
        const selectedMonth = month.get_value();
        const selectedLocation = budget_location.get_value();
        if (!selectedMonth || !selectedLocation) return;
        const request = ++loadRequest;
        body.html('<div class="text-muted p-3">Loading roster…</div>');
        const {message: data} = await frappe.call({
            method: 'vcmhrms.agency_worker.roster.get_month',
            args: {
                month: selectedMonth,
                budget_location: selectedLocation,
                agency: agency.get_value()
            }
        });
        if (request !== loadRequest) return;
        loadedData = data;
        loadedMonth = selectedMonth;
        renderRoster(data, selectedLocation, selectedMonth);
    }

    function renderRoster(data, selectedLocation, selectedMonth) {
        const dates = (data.dates || []).map(value => String(value).slice(0, 10));
        const lookup = new Map(data.rosters.map(row => [
            `${row.worker}|${String(row.roster_date).slice(0, 10)}`,
            row
        ]));
        let html = `<p><b>${esc(String(data.period_start).slice(0, 10))}</b> to <b>${esc(String(data.period_end).slice(0, 10))}</b>. Select staff across searches, then click <b>Bulk Assign Simple Shift</b>. The selection remains while you search. Click a day to edit OT, multiple shifts or other exceptions. OFF = weekly off.</p>`;
        html += '<table class="table table-bordered"><thead><tr>';
        html += '<th><input type="checkbox" class="select-all-workers" title="Select all visible staff"></th>';
        html += '<th>Agency Staff Name</th><th>Agency Staff ID</th><th>Agency</th>';
        dates.forEach(dateText => {
            const date = new Date(`${dateText}T00:00:00`);
            html += `<th>${dateText.slice(8, 10)} ${date.toLocaleDateString('en-US', {weekday: 'short'})}</th>`;
        });
        html += '</tr></thead><tbody>';
        data.workers.forEach((staff, index) => {
            const checked = selectedWorkerNames.has(staff.name) ? ' checked' : '';
            const searchText = esc(`${staff.worker_name} ${staff.staff_id} ${staff.agency}`);
            html += `<tr class="worker-row" data-search="${searchText}">`;
            html += `<td><input type="checkbox" class="worker-select" data-worker="${esc(staff.name)}"${checked}></td>`;
            html += `<th style="white-space:nowrap">${esc(staff.worker_name)}</th>`;
            html += `<td>${esc(staff.staff_id)}</td><td>${esc(staff.agency)}</td>`;
            dates.forEach(date => {
                const row = lookup.get(`${staff.name}|${date}`);
                const label = row ? (row.day_type === 'Weekly Off' ? 'OFF' : row.shifts.join(' + ')) : '+';
                html += `<td><button class="btn btn-xs ${label === 'OFF' ? 'btn-success' : 'btn-default'}" data-worker="${index}" data-date="${date}">${esc(label)}</button></td>`;
            });
            html += '</tr>';
        });
        html += '</tbody></table>';
        body.html(html);

        body.find('.worker-select').on('change', function() {
            if (this.checked) selectedWorkerNames.add(this.dataset.worker);
            else selectedWorkerNames.delete(this.dataset.worker);
            applyWorkerFilter();
        });
        body.find('.select-all-workers').on('change', function() {
            const shouldSelect = this.checked;
            body.find('tbody tr.worker-row:visible .worker-select').each(function() {
                this.checked = shouldSelect;
                if (shouldSelect) selectedWorkerNames.add(this.dataset.worker);
                else selectedWorkerNames.delete(this.dataset.worker);
            });
            updateSelectionSummary();
        });
        body.find('button[data-worker]').on('click', function() {
            const staff = data.workers[Number(this.dataset.worker)];
            const date = this.dataset.date;
            const row = lookup.get(`${staff.name}|${date}`);
            if (row) frappe.set_route('Form', 'Agency Roster', row.name);
            else frappe.new_doc('Agency Roster', {
                worker: staff.name,
                roster_date: date,
                budget_location: selectedLocation,
                day_type: 'Duty'
            });
        });
        applyWorkerFilter();
    }

    page.set_primary_action(__('Refresh Roster'), loadRoster);
    frappe.realtime.on('agency_roster_bulk_complete', message => {
        frappe.msgprint(__(`Bulk roster completed: ${message.created} created, ${message.replaced} replaced, ${message.skipped} skipped, ${message.failed_workers} workers failed.`));
        loadRoster();
    });
    setTimeout(loadRoster, 0);
};


