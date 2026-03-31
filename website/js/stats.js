/**
 * Backyard Hummers — Stats Page JS
 * Renders Chart.js charts and stat cards from site_data.json
 */

// Chart.js global defaults for the surveillance theme
Chart.defaults.color = '#9098a8';
Chart.defaults.borderColor = '#2a3648';
Chart.defaults.font.family = "'JetBrains Mono', monospace";
Chart.defaults.font.size = 11;

/**
 * Render the hourly distribution bar chart
 */
function renderHourlyChart(hourlyPattern) {
    const ctx = document.getElementById('hourlyChart');
    if (!ctx || !hourlyPattern) return;

    const labels = [];
    for (let i = 0; i < 24; i++) {
        const h = i % 12 || 12;
        const ampm = i < 12 ? 'A' : 'P';
        labels.push(`${h}${ampm}`);
    }

    // Pad array to 24 hours
    const data = Array.isArray(hourlyPattern) ? hourlyPattern : [];
    while (data.length < 24) data.push(0);

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'Detections',
                data: data,
                backgroundColor: data.map((v, i) => {
                    const max = Math.max(...data);
                    const ratio = max > 0 ? v / max : 0;
                    if (ratio > 0.8) return '#d4a017';
                    if (ratio > 0.5) return '#5cb84c';
                    return 'rgba(92, 184, 76, 0.4)';
                }),
                borderRadius: 3,
                borderSkipped: false,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 2.5,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => {
                            const i = items[0].dataIndex;
                            const h = i % 12 || 12;
                            const ampm = i < 12 ? 'AM' : 'PM';
                            return `${h}:00 ${ampm}`;
                        },
                        label: (item) => `${item.raw} detections`
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { precision: 0 },
                    grid: { color: 'rgba(42, 54, 72, 0.5)' }
                },
                x: {
                    grid: { display: false }
                }
            }
        }
    });
}

/**
 * Render the daily trend line chart
 */
function renderDailyChart(dailyCounts) {
    const ctx = document.getElementById('dailyChart');
    if (!ctx || !dailyCounts || !dailyCounts.length) return;

    const labels = dailyCounts.map(d => {
        try {
            const date = new Date(d.date + 'T00:00:00');
            return `${date.getMonth() + 1}/${date.getDate()}`;
        } catch {
            return d.date;
        }
    });

    const data = dailyCounts.map(d => d.total_detections || 0);

    new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Daily Detections',
                data: data,
                borderColor: '#5cb84c',
                backgroundColor: 'rgba(92, 184, 76, 0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 3,
                pointHoverRadius: 6,
                pointBackgroundColor: '#5cb84c',
                pointBorderColor: '#1e2a3a',
                pointBorderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 2.5,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (item) => `${item.raw} detections`
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { precision: 0 },
                    grid: { color: 'rgba(42, 54, 72, 0.5)' }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        maxTicksLimit: 10,
                        maxRotation: 0
                    }
                }
            }
        }
    });
}

/**
 * Populate stat cards
 */
function populateStats(data) {
    if (!data) return;

    // Key metrics
    const el = (id) => document.getElementById(id);

    const lifetime = el('statLifetime');
    if (lifetime) lifetime.textContent = (data.lifetime_detections || 0).toLocaleString();

    const today = el('statToday');
    if (today) today.textContent = data.today_detections || 0;

    const week = el('statWeek');
    if (week) week.textContent = data.this_week_detections || 0;

    // Next visit prediction
    const nextVisit = el('statNextVisit');
    const nextConf = el('statNextConfidence');
    if (nextVisit && data.next_predicted_visit) {
        nextVisit.textContent = formatTime(data.next_predicted_visit.time);
        if (nextConf) {
            const conf = (data.next_predicted_visit.confidence || 'unknown').toUpperCase();
            nextConf.textContent = `CONFIDENCE: ${conf}`;
        }
    }

    // Milestone progress
    if (data.milestones) {
        const fill = el('milestoneFill');
        const label = el('milestoneLabel');
        const latest = data.milestones.latest || 0;
        const next = data.milestones.next || 1000;
        const progress = next > 0 ? Math.min((data.lifetime_detections / next) * 100, 100) : 0;
        if (fill) fill.style.width = progress + '%';
        if (label) label.textContent = `Next milestone: ${next.toLocaleString()}`;
    }

    // Summary stats from hourly pattern
    if (data.hourly_pattern && data.hourly_pattern.some(v => v > 0)) {
        const hourly = data.hourly_pattern;
        const maxVal = Math.max(...hourly);
        const peakIdx = hourly.indexOf(maxVal);
        const peakHour = el('peakHour');
        if (peakHour && maxVal > 0) {
            const h = peakIdx % 12 || 12;
            const ampm = peakIdx < 12 ? 'AM' : 'PM';
            peakHour.textContent = `${h}:00 ${ampm}`;
        }
    }

    // Summary stats from daily counts
    if (data.daily_counts_30d && data.daily_counts_30d.length) {
        const daily = data.daily_counts_30d;
        const totals = daily.map(d => d.total_detections || 0);

        // Busiest day
        const maxDay = Math.max(...totals);
        const maxDayIdx = totals.indexOf(maxDay);
        const busiestDay = el('busiestDay');
        if (busiestDay && maxDay > 0) {
            try {
                const d = new Date(daily[maxDayIdx].date + 'T00:00:00');
                const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
                busiestDay.textContent = `${days[d.getDay()]} (${maxDay})`;
            } catch {
                busiestDay.textContent = `${maxDay} detections`;
            }
        }

        // Daily average
        const avg = totals.reduce((a, b) => a + b, 0) / totals.length;
        const dailyAvg = el('dailyAvg');
        if (dailyAvg) dailyAvg.textContent = avg.toFixed(1);
    }

    // Average gap
    if (data.avg_gap_minutes) {
        const avgGap = el('avgGap');
        if (avgGap) {
            const mins = Math.round(data.avg_gap_minutes);
            if (mins >= 60) {
                avgGap.textContent = `${Math.floor(mins / 60)}h ${mins % 60}m`;
            } else {
                avgGap.textContent = `${mins} min`;
            }
        }
    }
}

/**
 * Format a YYYY-MM-DD date string to a readable display
 */
function formatSeasonDate(dateStr) {
    if (!dateStr) return '--';
    try {
        const d = new Date(dateStr + 'T00:00:00');
        const months = ['January','February','March','April','May','June',
                        'July','August','September','October','November','December'];
        return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
    } catch {
        return dateStr;
    }
}

/**
 * Calculate days between two YYYY-MM-DD date strings
 */
function seasonLength(first, last) {
    if (!first || !last) return null;
    try {
        const d1 = new Date(first + 'T00:00:00');
        const d2 = new Date(last + 'T00:00:00');
        return Math.round((d2 - d1) / (1000 * 60 * 60 * 24));
    } catch {
        return null;
    }
}

/**
 * Render season prediction and history from site_data.json
 */
function renderSeasonData(data) {
    const seasons = data.season_dates;
    if (!seasons || !seasons.length) return;

    // Season History Table
    const section = document.getElementById('seasonHistorySection');
    const tbody = document.getElementById('seasonTableBody');
    if (section && tbody) {
        section.style.display = '';
        tbody.innerHTML = '';
        for (const s of seasons) {
            const len = seasonLength(s.first_visit, s.last_visit);
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--border)';
            tr.innerHTML = `
                <td style="padding:10px; font-weight:bold;">${s.year}</td>
                <td style="padding:10px; color:#5cb84c;">${formatSeasonDate(s.first_visit)}</td>
                <td style="padding:10px; color:#d4a017;">${formatSeasonDate(s.last_visit)}</td>
                <td style="padding:10px; color:var(--text-muted);">${len !== null ? len + ' days' : '--'}</td>
            `;
            tbody.appendChild(tr);
        }
    }

    // Season Arrival Prediction
    // Compute from the season_dates: average first-visit day-of-year
    const withFirst = seasons.filter(s => s.first_visit);
    if (withFirst.length < 2) return;

    const doys = withFirst.map(s => {
        const d = new Date(s.first_visit + 'T00:00:00');
        const start = new Date(d.getFullYear(), 0, 0);
        return Math.floor((d - start) / (1000 * 60 * 60 * 24));
    });

    const meanDoy = Math.round(doys.reduce((a, b) => a + b, 0) / doys.length);
    const minDoy = Math.min(...doys);
    const maxDoy = Math.max(...doys);

    const now = new Date();
    let targetYear = now.getFullYear();
    const predicted = new Date(targetYear, 0, meanDoy);
    const earliest = new Date(targetYear, 0, minDoy);
    const latest = new Date(targetYear, 0, maxDoy);

    // If we're well past the latest date, show next year
    const latestPlusBuffer = new Date(latest);
    latestPlusBuffer.setDate(latestPlusBuffer.getDate() + 30);
    if (now > latestPlusBuffer) {
        targetYear++;
        predicted.setFullYear(targetYear);
        earliest.setFullYear(targetYear);
        latest.setFullYear(targetYear);
    }

    const daysUntil = Math.ceil((predicted - now) / (1000 * 60 * 60 * 24));

    // Check if currently in season
    const currentYearSeason = seasons.find(s => s.year === now.getFullYear());
    let inSeason = false;
    if (currentYearSeason && currentYearSeason.first_visit) {
        const firstDt = new Date(currentYearSeason.first_visit + 'T00:00:00');
        const lastDt = currentYearSeason.last_visit ? new Date(currentYearSeason.last_visit + 'T00:00:00') : null;
        if (now >= firstDt && (!lastDt || now <= lastDt)) inSeason = true;
    }

    // Avg season length
    const withBoth = seasons.filter(s => s.first_visit && s.last_visit);
    const avgLen = withBoth.length > 0
        ? Math.round(withBoth.map(s => seasonLength(s.first_visit, s.last_visit)).reduce((a, b) => a + b, 0) / withBoth.length)
        : null;

    const months = ['January','February','March','April','May','June',
                    'July','August','September','October','November','December'];
    const predDisplay = `${months[predicted.getMonth()]} ${predicted.getDate()}`;
    const earlyDisplay = `${months[earliest.getMonth()]} ${earliest.getDate()}`;
    const lateDisplay = `${months[latest.getMonth()]} ${latest.getDate()}`;

    const predSection = document.getElementById('seasonPredictionSection');
    const predContent = document.getElementById('seasonPredictionContent');
    if (!predSection || !predContent) return;
    predSection.style.display = '';

    if (inSeason) {
        predContent.innerHTML = `
            <span class="metric-value" style="color:#5cb84c;">Season is Active!</span>
            ${avgLen ? `<div style="color:var(--text-muted); margin-top:8px;">Average season length: ${avgLen} days</div>` : ''}
        `;
    } else if (daysUntil > 0) {
        predContent.innerHTML = `
            <div style="color:var(--text-muted); margin-bottom:8px;">Hummingbirds typically arrive around</div>
            <span class="metric-value">${predDisplay}</span>
            <div style="margin-top:10px;">
                <span style="color:#d4a017; font-size:1.3em; font-weight:bold;">${daysUntil} days to go!</span>
            </div>
            <div style="color:var(--text-muted); margin-top:6px; font-size:0.85em;">
                Based on ${withFirst.length} years of data (earliest: ${earlyDisplay}, latest: ${lateDisplay})
            </div>
        `;
    } else {
        predContent.innerHTML = `
            <div style="color:var(--text-muted); margin-bottom:8px;">Hummingbirds typically arrive around</div>
            <span class="metric-value">${predDisplay}</span>
            <div style="color:var(--text-muted); margin-top:6px; font-size:0.85em;">
                Based on ${withFirst.length} years of data (earliest: ${earlyDisplay}, latest: ${lateDisplay})
            </div>
        `;
    }
}

/**
 * Initialize stats page
 */
document.addEventListener('DOMContentLoaded', async () => {
    if (!siteData) {
        await loadSiteData();
    }
    const data = siteData;
    if (!data) return;

    populateStats(data);
    renderHourlyChart(data.hourly_pattern);
    renderDailyChart(data.daily_counts_30d);
    renderSeasonData(data);
});
