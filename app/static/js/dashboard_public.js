(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        const commonOptions = {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        boxWidth: 12,
                        padding: 16,
                        font: { family: '"Noto Sans", sans-serif' }
                    }
                }
            }
        };

        const statusBadgeMap = {
            'Pending': 'badge-pending',
            'Under Review': 'badge-review',
            'Action Taken': 'badge-action',
            'Delayed': 'badge-delayed',
            'Reopened': 'badge-reopened',
            'Closed': 'badge-closed'
        };

        const metricConfig = [
            { id: 'metricTotal', key: 'total', decimals: 0 },
            { id: 'metricPending', key: 'pending', decimals: 0 },
            { id: 'metricInProgress', key: 'in_progress', decimals: 0 },
            { id: 'metricResolved', key: 'closed', decimals: 0 },
            { id: 'metricHighPriority', key: 'high_priority', decimals: 0 },
            { id: 'metricSlaCompliance', key: 'sla_compliance', decimals: 2 }
        ];

        const chartInstances = {};
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        let activeOverviewController = null;
        const activeChartControllers = {};
        let dashboardRequestSeq = 0;

        const filterEls = {
            department: document.getElementById('dashboardDeptFilter'),
            status: document.getElementById('dashboardStatusFilter'),
            fromMonth: document.getElementById('dashboardFromMonth'),
            toMonth: document.getElementById('dashboardToMonth'),
            applyBtn: document.getElementById('dashboardApplyBtn'),
            resetBtn: document.getElementById('dashboardResetBtn'),
            error: document.getElementById('dashboardFilterError')
        };

        function sumNumbers(arr) {
            if (!Array.isArray(arr)) {
                return 0;
            }
            return arr.reduce(function (acc, value) {
                return acc + (Number(value) || 0);
            }, 0);
        }

        function isDatasetSeriesEmpty(data) {
            if (!data || !Array.isArray(data.labels) || !data.labels.length) {
                return true;
            }
            const datasets = data.datasets || [];
            if (!datasets.length) {
                return true;
            }
            return datasets.every(function (ds) {
                return sumNumbers(ds.data) === 0;
            });
        }

        function clearChartIfExists(key) {
            if (chartInstances[key]) {
                chartInstances[key].destroy();
                delete chartInstances[key];
            }
        }

        function setCanvasVisible(canvasId, visible) {
            const canvas = document.getElementById(canvasId);
            if (!canvas) {
                return;
            }
            canvas.classList.toggle('d-none', !visible);
            const wrap = canvas.closest('.chart-canvas-wrap');
            if (!wrap) {
                return;
            }
            const hint = wrap.querySelector('.chart-empty-hint');
            if (hint) {
                hint.remove();
            }
            if (!visible) {
                const empty = document.createElement('div');
                empty.className = 'chart-empty-hint';
                empty.setAttribute('role', 'status');
                wrap.appendChild(empty);
                return empty;
            }
            return null;
        }

        function showChartEmptyMessage(canvasId, message) {
            const emptyEl = setCanvasVisible(canvasId, false);
            if (emptyEl) {
                emptyEl.textContent = message;
            }
        }

        function escapeHtml(value) {
            return String(value || '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function buildQuery(filters) {
            const params = new URLSearchParams();
            Object.keys(filters).forEach(function (key) {
                const value = filters[key];
                if (value !== null && value !== undefined && value !== '') {
                    params.set(key, value);
                }
            });
            return params.toString();
        }

        function getActiveFilters() {
            return {
                department_id: filterEls.department?.value || '',
                status: filterEls.status?.value || '',
                from_month: filterEls.fromMonth?.value || '',
                to_month: filterEls.toMonth?.value || ''
            };
        }

        function setFilterError(message) {
            if (!filterEls.error) {
                return;
            }
            if (!message) {
                filterEls.error.classList.add('d-none');
                filterEls.error.textContent = '';
                return;
            }
            filterEls.error.classList.remove('d-none');
            filterEls.error.textContent = message;
        }

        function validateFilters(filters) {
            if (filters.from_month && filters.to_month && filters.from_month > filters.to_month) {
                setFilterError('From month must be before or equal to To month.');
                return false;
            }
            setFilterError('');
            return true;
        }

        function setChartState(stateId, message, isError = false) {
            const el = document.getElementById(stateId);
            if (!el) {
                return;
            }
            if (!message) {
                el.classList.add('d-none');
                return;
            }
            el.classList.remove('d-none');
            el.classList.toggle('chart-error', isError);
            el.innerHTML = isError
                ? `<i class="bi bi-exclamation-triangle me-2" aria-hidden="true"></i>${message}`
                : `<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>${message}`;
        }

        function createOrUpdateChart(key, canvasId, config) {
            clearChartIfExists(key);
            const canvas = document.getElementById(canvasId);
            if (!canvas) {
                return;
            }
            canvas.classList.remove('d-none');
            const wrap = canvas.closest('.chart-canvas-wrap');
            if (wrap) {
                const hint = wrap.querySelector('.chart-empty-hint');
                if (hint) {
                    hint.remove();
                }
            }
            chartInstances[key] = new Chart(canvas, config);
        }

        function loadChart(url, stateId, key, renderer, filters, requestId) {
            const query = buildQuery(filters || {});
            const endpoint = query ? `${url}?${query}` : url;
            const controller = new AbortController();

            if (activeChartControllers[key]) {
                activeChartControllers[key].abort();
            }
            activeChartControllers[key] = controller;

            setChartState(stateId, 'Loading chart data...');

            return fetch(endpoint, {
                cache: 'default',
                signal: controller.signal
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error(`Failed to load: ${response.status}`);
                    }
                    return response.json();
                })
                .then(function (data) {
                    if (requestId !== dashboardRequestSeq) {
                        return false;
                    }
                    renderer(data, key);
                    setChartState(stateId, '');
                    return true;
                })
                .catch(function (error) {
                    if (error?.name === 'AbortError' || requestId !== dashboardRequestSeq) {
                        return false;
                    }
                    setChartState(stateId, 'Unable to load chart data right now.', true);
                    return false;
                })
                .finally(function () {
                    if (activeChartControllers[key] === controller) {
                        delete activeChartControllers[key];
                    }
                });
        }

        function formatDashboardTimestamp() {
            const now = new Date();
            return new Intl.DateTimeFormat('en-IN', {
                dateStyle: 'medium',
                timeStyle: 'short'
            }).format(now);
        }

        function initExportMonthControl() {
            const monthInput = document.getElementById('exportMonth');
            const exportBtn = document.getElementById('exportCsvBtn');
            if (!monthInput || !exportBtn) {
                return;
            }

            const now = new Date();
            const currentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
            monthInput.value = currentMonth;

            function syncHref() {
                const monthValue = monthInput.value || currentMonth;
                exportBtn.href = `/api/public/export/monthly.csv?month=${encodeURIComponent(monthValue)}`;
            }

            monthInput.addEventListener('change', syncHref);
            syncHref();
        }

        function animateValue(el, target, decimals = 0) {
            if (!el) {
                return;
            }

            const safeTarget = Number(target || 0);
            const startValue = Number(el.dataset.value || 0);

            if (prefersReducedMotion) {
                el.textContent = decimals > 0 ? safeTarget.toFixed(decimals) : Math.round(safeTarget).toString();
                el.dataset.value = safeTarget.toString();
                return;
            }

            const startTs = performance.now();
            const duration = 650;

            function step(ts) {
                const progress = Math.min((ts - startTs) / duration, 1);
                const eased = 1 - Math.pow(1 - progress, 3);
                const current = startValue + ((safeTarget - startValue) * eased);
                el.textContent = decimals > 0 ? current.toFixed(decimals) : Math.round(current).toString();

                if (progress < 1) {
                    requestAnimationFrame(step);
                    return;
                }
                el.dataset.value = safeTarget.toString();
            }

            requestAnimationFrame(step);
        }

        function initMetricCounters() {
            metricConfig.forEach(function (item) {
                const el = document.getElementById(item.id);
                if (!el) {
                    return;
                }
                const target = Number(el.getAttribute('data-count') || el.textContent || 0);
                el.dataset.value = '0';
                animateValue(el, target, item.decimals);
            });
        }

        function updateMetrics(stats) {
            metricConfig.forEach(function (item) {
                const value = Number(stats[item.key] || 0);
                animateValue(document.getElementById(item.id), value, item.decimals);
            });
        }

        function updateInsightCards(stats, activeDepartments) {
            const delayedEl = document.getElementById('insightDelayedValue');
            const reopenedEl = document.getElementById('insightReopenedValue');
            const backlogEl = document.getElementById('insightBacklogValue');
            const activeDeptPill = document.getElementById('activeDepartmentsPill');
            const sentimentNegative = document.getElementById('sentimentNegative');
            const sentimentUrgent = document.getElementById('sentimentUrgent');
            const sentimentRepeated = document.getElementById('sentimentRepeated');
            const qualityAvgResolution = document.getElementById('qualityAvgResolution');
            const qualityFeedbackRate = document.getElementById('qualityFeedbackRate');

            if (delayedEl) {
                delayedEl.textContent = `${stats.delayed || 0} delayed complaints`;
            }
            if (reopenedEl) {
                reopenedEl.textContent = `${stats.reopened || 0} reopened complaints`;
            }
            if (backlogEl) {
                const backlog = Number(stats.backlog_rate || 0);
                backlogEl.textContent = `${backlog.toFixed(2)}%`;
            }
            if (activeDeptPill) {
                activeDeptPill.innerHTML = `<i class="bi bi-check-circle me-1" aria-hidden="true"></i>${activeDepartments || 0} active departments`;
            }
            if (sentimentNegative) {
                sentimentNegative.textContent = `${Number(stats.negative_percent || 0).toFixed(2)}%`;
            }
            if (sentimentUrgent) {
                sentimentUrgent.textContent = `${Number(stats.urgent_percent || 0).toFixed(2)}%`;
            }
            if (sentimentRepeated) {
                sentimentRepeated.textContent = `${Number(stats.repeated_percent || 0).toFixed(2)}%`;
            }
            if (qualityAvgResolution) {
                qualityAvgResolution.textContent = `${Number(stats.avg_resolution_hours || 0).toFixed(2)} hrs`;
            }
            if (qualityFeedbackRate) {
                qualityFeedbackRate.textContent = `${Number(stats.feedback_rate || 0).toFixed(2)}%`;
            }
        }

        function updateBestWorstDepartments(bestDepartment, worstDepartment) {
            const bestName = document.getElementById('bestDepartmentName');
            const bestScore = document.getElementById('bestDepartmentScore');
            const worstName = document.getElementById('worstDepartmentName');
            const worstScore = document.getElementById('worstDepartmentScore');

            if (bestName) {
                bestName.textContent = bestDepartment?.name || 'N/A';
            }
            if (bestScore) {
                bestScore.textContent = bestDepartment ? bestDepartment.score : 'N/A';
            }
            if (worstName) {
                worstName.textContent = worstDepartment?.name || 'N/A';
            }
            if (worstScore) {
                worstScore.textContent = worstDepartment ? worstDepartment.score : 'N/A';
            }
        }

        function renderDeptScoreboard(deptStats) {
            const grid = document.getElementById('deptScoreboardGrid');
            if (!grid) {
                return;
            }

            if (!Array.isArray(deptStats) || !deptStats.length) {
                grid.innerHTML = '<div class="col-12"><div class="alert alert-warning mb-0">No department records match the selected filters.</div></div>';
                return;
            }

            const html = deptStats.map(function (dept, index) {
                const resolutionRate = Number(dept.resolution_rate || 0);
                return `
                    <div class="col-md-6 col-xl-4">
                        <div class="dept-rank-card h-100">
                            <div class="dept-rank-head mb-3">
                                <div>
                                    <h6 class="fw-semibold mb-1">${escapeHtml(dept.name)}</h6>
                                    <p class="small text-muted mb-0">${Number(dept.total || 0)} total complaints</p>
                                </div>
                                <span class="dept-rank-badge">#${index + 1}</span>
                            </div>
                            <div class="row g-2 text-center mb-3">
                                <div class="col-4"><div class="dept-rank-stat"><div class="fw-bold">${Number(dept.pending || 0)}</div><small class="text-muted">Pending</small></div></div>
                                <div class="col-4"><div class="dept-rank-stat dept-rank-stat-success"><div class="fw-bold text-success">${Number(dept.closed || 0)}</div><small class="text-muted">Closed</small></div></div>
                                <div class="col-4"><div class="dept-rank-stat dept-rank-stat-danger"><div class="fw-bold text-danger">${Number(dept.delayed || 0)}</div><small class="text-muted">Delayed</small></div></div>
                            </div>
                            <div class="d-flex justify-content-between small mb-1"><span>Resolution Rate</span><span class="fw-semibold">${resolutionRate.toFixed(1)}%</span></div>
                            <div class="progress" style="height: 6px;"><div class="progress-bar bg-success" style="width: ${Math.max(0, Math.min(100, resolutionRate))}%"></div></div>
                            <div class="d-flex justify-content-between small mt-2"><span class="text-muted">Delay penalty ${Number(dept.delay_penalty || 0).toFixed(1)}%</span><span class="fw-semibold">Score ${Number(dept.score || 0).toFixed(1)}</span></div>
                        </div>
                    </div>
                `;
            }).join('');

            grid.innerHTML = html;
        }

        function renderTopServices(topServices) {
            const container = document.getElementById('topServicesPanel');
            if (!container) {
                return;
            }

            if (!Array.isArray(topServices) || !topServices.length) {
                container.innerHTML = '<p class="small text-muted mb-0">No service activity yet.</p>';
                return;
            }

            const html = topServices
                .slice(0, 6)
                .map(function (item) {
                    const count = Number(item.count || 0);
                    const name = escapeHtml(item.name || 'Unknown Service');
                    return (
                        `<div class="dashboard-insight-row">`
                        + `<span class="small text-muted text-truncate d-inline-block" style="max-width: 190px;">${name}</span>`
                        + `<strong>${count}</strong>`
                        + `</div>`
                    );
                })
                .join('');

            container.innerHTML = html;
        }

        function renderRecentActivity(rows) {
            const tbody = document.getElementById('recentActivityBody');
            if (!tbody) {
                return;
            }

            if (!Array.isArray(rows) || !rows.length) {
                tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">No recent activity for current selection.</td></tr>';
                return;
            }

            const html = rows.map(function (item) {
                const badgeClass = item.status_badge || statusBadgeMap[item.status] || 'badge-secondary';
                return `
                    <tr>
                        <td><code>${escapeHtml(item.public_reference || 'Public record')}</code></td>
                        <td>${escapeHtml(item.department)}</td>
                        <td>${escapeHtml(item.service)}</td>
                        <td><span class="badge-status ${badgeClass}">${escapeHtml(item.status)}</span></td>
                        <td>${escapeHtml(item.submitted_at)}</td>
                    </tr>
                `;
            }).join('');

            tbody.innerHTML = html;
        }

        function applyOverviewPayload(payload) {
            const stats = payload?.stats || {};
            updateMetrics(stats);
            updateInsightCards(stats, payload?.active_departments || 0);
            updateBestWorstDepartments(payload?.best_department, payload?.worst_department);
            renderDeptScoreboard(payload?.dept_stats || []);
            renderRecentActivity(payload?.recent_complaints || []);
            renderTopServices(payload?.top_services || []);
        }

        function fetchOverview(filters, requestId) {
            const query = buildQuery(filters);
            const endpoint = query ? `/api/dashboard/overview?${query}` : '/api/dashboard/overview';
            const controller = new AbortController();

            if (activeOverviewController) {
                activeOverviewController.abort();
            }
            activeOverviewController = controller;

            return fetch(endpoint, {
                cache: 'default',
                signal: controller.signal
            })
                .then(function (response) {
                    return response.json().catch(function () { return {}; }).then(function (payload) {
                        if (!response.ok) {
                            throw new Error(payload.error || 'Unable to load filtered overview.');
                        }
                        if (requestId !== dashboardRequestSeq) {
                            return false;
                        }
                        applyOverviewPayload(payload);
                        return true;
                    });
                })
                .catch(function (error) {
                    if (error?.name === 'AbortError' || requestId !== dashboardRequestSeq) {
                        return false;
                    }
                    throw error;
                })
                .finally(function () {
                    if (activeOverviewController === controller) {
                        activeOverviewController = null;
                    }
                });
        }
        function renderMonthlyChart(data, key) {
            if (!data || isDatasetSeriesEmpty({ labels: data.labels, datasets: [{ data: data.data }] })) {
                clearChartIfExists(key);
                showChartEmptyMessage('monthlyChart', 'No monthly complaint data for the selected filters yet.');
                return;
            }
            const canvas = document.getElementById('monthlyChart');
            const ctx = canvas.getContext('2d');
            const gradient = ctx.createLinearGradient(0, 0, 0, 320);
            const isContrast = document.documentElement.getAttribute('data-theme') === 'contrast';
            const lineColor = isContrast ? '#60a5fa' : '#0b5ed7';
            if (isContrast) {
                gradient.addColorStop(0, 'rgba(96, 165, 250, 0.25)');
                gradient.addColorStop(1, 'rgba(96, 165, 250, 0.02)');
            } else {
                gradient.addColorStop(0, 'rgba(11, 94, 215, 0.24)');
                gradient.addColorStop(1, 'rgba(11, 94, 215, 0.02)');
            }

            createOrUpdateChart(key, 'monthlyChart', {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: 'Complaints',
                        data: data.data,
                        borderColor: lineColor,
                        backgroundColor: gradient,
                        borderWidth: 2,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        fill: true,
                        tension: 0.35
                    }]
                },
                options: {
                    ...commonOptions,
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: { stepSize: 1 }
                        }
                    }
                }
            });
        }

        function renderStatusChart(data, key) {
            if (!data || isDatasetSeriesEmpty({ labels: data.labels, datasets: [{ data: data.data }] })) {
                clearChartIfExists(key);
                showChartEmptyMessage('statusChart', 'No status breakdown for the selected filters yet.');
                return;
            }
            const isContrast = document.documentElement.getAttribute('data-theme') === 'contrast';
            const doughnutColors = isContrast 
                ? ['#fbbf24', '#22d3ee', '#60a5fa', '#f87171', '#94a3b8', '#34d399']
                : ['#f59e0b', '#06b6d4', '#0b5ed7', '#ef4444', '#64748b', '#16a34a'];
            createOrUpdateChart(key, 'statusChart', {
                type: 'doughnut',
                data: {
                    labels: data.labels,
                    datasets: [{
                        data: data.data,
                        backgroundColor: doughnutColors,
                        borderWidth: 0
                    }]
                },
                options: commonOptions
            });
        }

        function renderDeptChart(data, key) {
            if (!data || isDatasetSeriesEmpty({ labels: data.labels, datasets: [{ data: data.data }] })) {
                clearChartIfExists(key);
                showChartEmptyMessage('deptChart', 'No department workload data for the selected filters yet.');
                return;
            }
            const isContrast = document.documentElement.getAttribute('data-theme') === 'contrast';
            const barBg = isContrast ? '#60a5fa' : 'rgba(11, 94, 215, 0.85)';
            createOrUpdateChart(key, 'deptChart', {
                type: 'bar',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: 'Total Complaints',
                        data: data.data,
                        backgroundColor: barBg,
                        borderRadius: 8
                    }]
                },
                options: {
                    ...commonOptions,
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: { stepSize: 1 }
                        }
                    }
                }
            });
        }

        function renderResolutionTimeChart(data, key) {
            if (!data || isDatasetSeriesEmpty({ labels: data.labels, datasets: [{ data: data.data }] })) {
                clearChartIfExists(key);
                showChartEmptyMessage('resolutionTimeChart', 'No resolution time trend for the selected filters yet.');
                return;
            }
            const isContrast = document.documentElement.getAttribute('data-theme') === 'contrast';
            const lineColor = isContrast ? '#22d3ee' : '#0891b2';
            const bgColor = isContrast ? 'rgba(34, 211, 238, 0.15)' : 'rgba(8, 145, 178, 0.15)';
            createOrUpdateChart(key, 'resolutionTimeChart', {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: 'Avg Hours',
                        data: data.data,
                        borderColor: lineColor,
                        backgroundColor: bgColor,
                        borderWidth: 2,
                        pointRadius: 2,
                        fill: true,
                        tension: 0.3
                    }]
                },
                options: {
                    ...commonOptions,
                    plugins: { legend: { display: false } },
                    scales: { y: { beginAtZero: true } }
                }
            });
        }

        function renderSlaComplianceChart(data, key) {
            if (!data || isDatasetSeriesEmpty({ labels: data.labels, datasets: [{ data: data.data }] })) {
                clearChartIfExists(key);
                showChartEmptyMessage('slaComplianceChart', 'No SLA compliance trend for the selected filters yet.');
                return;
            }
            const isContrast = document.documentElement.getAttribute('data-theme') === 'contrast';
            const barBg = isContrast ? '#34d399' : 'rgba(16, 185, 129, 0.86)';
            createOrUpdateChart(key, 'slaComplianceChart', {
                type: 'bar',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: 'SLA %',
                        data: data.data,
                        backgroundColor: barBg,
                        borderRadius: 8
                    }]
                },
                options: {
                    ...commonOptions,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: {
                            beginAtZero: true,
                            max: 100
                        }
                    }
                }
            });
        }

        function refreshCharts(filters, requestId) {
            return Promise.allSettled([
                loadChart('/api/chart/monthly', 'monthlyChartState', 'monthly', renderMonthlyChart, filters, requestId),
                loadChart('/api/chart/status', 'statusChartState', 'status', renderStatusChart, filters, requestId),
                loadChart('/api/chart/dept', 'deptChartState', 'dept', renderDeptChart, filters, requestId),
                loadChart('/api/chart/resolution-time', 'resolutionTimeChartState', 'resolutionTime', renderResolutionTimeChart, filters, requestId),
                loadChart('/api/chart/sla-compliance', 'slaComplianceChartState', 'slaCompliance', renderSlaComplianceChart, filters, requestId)
            ]);
        }

        function setFilterLoading(isLoading) {
            if (!filterEls.applyBtn || !filterEls.resetBtn) {
                return;
            }

            filterEls.applyBtn.disabled = isLoading;
            filterEls.resetBtn.disabled = isLoading;
            if (isLoading) {
                filterEls.applyBtn.dataset.originalHtml = filterEls.applyBtn.innerHTML;
                filterEls.applyBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Applying';
            } else if (filterEls.applyBtn.dataset.originalHtml) {
                filterEls.applyBtn.innerHTML = filterEls.applyBtn.dataset.originalHtml;
            }
        }

        function updateDashboardTimestamp() {
            const updated = document.getElementById('dashboardUpdatedAt');
            if (updated) {
                updated.textContent = `Updated: ${formatDashboardTimestamp()}`;
            }
        }

        function applyDashboardFilters() {
            const filters = getActiveFilters();
            if (!validateFilters(filters)) {
                return;
            }

            const requestId = ++dashboardRequestSeq;
            setFilterLoading(true);
            Promise.all([fetchOverview(filters, requestId), refreshCharts(filters, requestId)])
                .catch(function (error) {
                    setFilterError(error.message || 'Unable to apply filters right now.');
                })
                .finally(function () {
                    if (requestId === dashboardRequestSeq) {
                        setFilterLoading(false);
                        updateDashboardTimestamp();
                    }
                });
        }

        function resetDashboardFilters() {
            if (filterEls.department) {
                filterEls.department.value = '';
            }
            if (filterEls.status) {
                filterEls.status.value = '';
            }
            if (filterEls.fromMonth) {
                filterEls.fromMonth.value = '';
            }
            if (filterEls.toMonth) {
                filterEls.toMonth.value = '';
            }
            setFilterError('');
            applyDashboardFilters();
        }

        function updateChartDefaultsForTheme() {
            const isContrast = document.documentElement.getAttribute('data-theme') === 'contrast';
            if (isContrast) {
                Chart.defaults.color = '#f8fafc';
                Chart.defaults.borderColor = '#475569';
            } else {
                Chart.defaults.color = '#64748b';
                Chart.defaults.borderColor = '#e2e8f0';
            }
        }

        window.addEventListener('themechanged', function () {
            updateChartDefaultsForTheme();
            applyDashboardFilters();
        });

        updateChartDefaultsForTheme();
        initExportMonthControl();
        initMetricCounters();

        if (filterEls.applyBtn) {
            filterEls.applyBtn.addEventListener('click', applyDashboardFilters);
        }
        if (filterEls.resetBtn) {
            filterEls.resetBtn.addEventListener('click', resetDashboardFilters);
        }

        applyDashboardFilters();
    });
})();
